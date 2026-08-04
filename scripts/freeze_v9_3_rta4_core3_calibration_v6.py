#!/usr/bin/env python3
"""Freeze an explicit reviewed CORE-3 H_rel/B_low/B_high choice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_core3_calibration_v6 import (  # noqa: E402
    RTA4Core3CalibrationV6Error,
    freeze_calibration_v6,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-horizon", type=int, required=True)
    parser.add_argument("--b-low", required=True)
    parser.add_argument("--b-high", required=True)
    args = parser.parse_args()
    try:
        value = freeze_calibration_v6(
            args.summary,
            args.output,
            release_horizon=args.release_horizon,
            b_low=args.b_low,
            b_high=args.b_high,
        )
    except (OSError, RTA4Core3CalibrationV6Error) as exc:
        print(f"CORE-3 calibration freeze failed: {exc}")
        return 1
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
