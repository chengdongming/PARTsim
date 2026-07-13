#!/usr/bin/env python3
"""Thin CORE-1 wrapper around the common v9.3 production engine."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.cli import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli("CORE-1"))
