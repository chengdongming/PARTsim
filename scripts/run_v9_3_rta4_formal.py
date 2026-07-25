#!/usr/bin/env python3
"""Dry-run or guard the opt-in v9.3 RTA4 formal experiment plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config import load_rta4_formal_config
from experiments.v9_3.rta4_formal_pipeline import (
    RTA4FormalAuthorizationError, RTA4FormalRunner,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = load_rta4_formal_config(args.config)
    runner = RTA4FormalRunner(config)
    if args.dry_run:
        print(json.dumps(runner.describe(), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    try:
        runner.run()
    except RTA4FormalAuthorizationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
