#!/usr/bin/env python3
"""Rebuild CORE-4 summaries from persisted CSV files only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.core4_aggregation import aggregate_core4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(aggregate_core4(args.run_root), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
