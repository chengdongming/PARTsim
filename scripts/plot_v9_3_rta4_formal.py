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

from experiments.v9_3.rta4_formal_aggregation import validate_aggregate_bundle
from experiments.v9_3.rta4_formal_environment import validate_command_invocation
from experiments.v9_3.rta4_formal_plotting import render_formal_publication_figures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = validate_aggregate_bundle(args.aggregate_root)
    if (
        manifest.get("execution_class") != "FORMAL_AUTHORIZED"
        or not manifest.get("authorization_id")
    ):
        print(
            "publication plotting requires an authorized formal aggregate",
            file=sys.stderr,
        )
        return 2
    try:
        validate_command_invocation(
            manifest["command_manifest"], argv=sys.argv,
            cwd=Path.cwd(), operation="plot", core=manifest["core"],
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = render_formal_publication_figures(args.aggregate_root, args.output_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
