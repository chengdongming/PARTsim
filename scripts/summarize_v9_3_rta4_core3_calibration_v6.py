#!/usr/bin/env python3
"""Summarize paired CORE-3 calibration evidence without selecting values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.result_writer import atomic_write_json  # noqa: E402
from experiments.v9_3.rta4_core3_calibration_v6 import (  # noqa: E402
    RTA4Core3CalibrationV6Error,
    summarize_calibration_v6,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-manifest", type=Path, required=True)
    parser.add_argument("--run-root-30000", type=Path, required=True)
    parser.add_argument("--run-root-60000", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = summarize_calibration_v6(
            args.calibration_manifest,
            args.run_root_30000,
            args.run_root_60000,
        )
        atomic_write_json(args.output, summary)
    except (OSError, RTA4Core3CalibrationV6Error) as exc:
        print(f"CORE-3 calibration summary failed: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    if summary["pairing_complete"] is not True:
        print("CORE-3 calibration summary is incomplete")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
