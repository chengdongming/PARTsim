#!/usr/bin/env python3
"""Generate the two paired CORE-3 calibration campaigns; never run them."""

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
    write_calibration_campaigns_v6,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = write_calibration_campaigns_v6(args.config, args.output_root)
    except (OSError, RTA4Core3CalibrationV6Error) as exc:
        print(f"CORE-3 calibration generation failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
