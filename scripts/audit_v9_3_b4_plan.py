#!/usr/bin/env python3
"""Audit B4 plan cardinality and duplicate identities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.performance_audit import audit_plan_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("CALIBRATION", "HORIZON_GATE", "FORMAL"), required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_plan_counts(args.stage, json.loads(args.plan.read_text(encoding="utf-8")))
    print(json.dumps(audit, sort_keys=True))
    return 0 if audit["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
