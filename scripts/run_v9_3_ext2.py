#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from experiments.v9_3.ext2_real_trace import Ext2Runner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-cells", action="store_true")
    parser.add_argument("--max-cells", type=int)
    parser.add_argument("--max-tasksets", type=int)
    args = parser.parse_args()
    runner = Ext2Runner.from_path(args.config)
    if args.dry_run or args.list_cells:
        value = runner.describe(max_cells=args.max_cells, max_tasksets=args.max_tasksets)
        if not args.list_cells:
            value.pop("cells", None)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    outcome = runner.run(
        resume=args.resume or runner.config["execution"]["resume"],
        max_cells=args.max_cells, max_tasksets=args.max_tasksets,
    )
    print(json.dumps(dict(outcome.summary), ensure_ascii=False, sort_keys=True))
    return 2 if outcome.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
