#!/usr/bin/env python3
"""Rebuild CORE-1 summaries from persisted CSVs; never calls the solver."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.aggregation import aggregate_core1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(aggregate_core1(args.run_root), ensure_ascii=False, sort_keys=True))
