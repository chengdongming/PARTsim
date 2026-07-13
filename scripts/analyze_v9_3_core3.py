#!/usr/bin/env python3
"""Rebuild CORE-3 summaries without invoking RTA or simulation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.config import load_config
from experiments.v9_3.core3_pairing import analyze_core3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    config = load_config(args.run_root / "run_config.yaml", expected_core="CORE-3")
    print(json.dumps(
        analyze_core3(config), ensure_ascii=False, sort_keys=True, indent=2
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
