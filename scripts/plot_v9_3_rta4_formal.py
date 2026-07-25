#!/usr/bin/env python3
"""Render ASAP-BLOCK v9.3 RTA4 figures from a validated aggregate bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_plotting import render_formal_publication_figures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = render_formal_publication_figures(args.aggregate_root, args.output_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
