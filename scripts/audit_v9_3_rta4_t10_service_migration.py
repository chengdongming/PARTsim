#!/usr/bin/env python3
"""CLI wrapper for the RTA4 T10 Stage-A.5 service migration audit."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_t10_service_migration_audit import main


if __name__ == "__main__":
    raise SystemExit(main())
