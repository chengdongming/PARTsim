#!/usr/bin/env python3
"""Rebuild EXT-1B aggregate/statistical outputs from terminal CSVs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_engine import analyze_ext1b, verify_file_hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()
    summary = dict(analyze_ext1b(args.output_root))
    if args.verify_hashes:
        summary["file_hashes_valid"] = verify_file_hashes(args.output_root)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("file_hashes_valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
