#!/usr/bin/env python3
"""Rebuild CORE-5 resource/censoring summaries from persisted files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.core5_aggregation import analyze_core5_artifacts
from experiments.v9_3.core5_contract import Core5ContractError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    try:
        summary = analyze_core5_artifacts(args.run_root)
    except (Core5ContractError, OSError, ValueError) as exc:
        print(f"CORE-5 analyzer rejected artifact root: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
