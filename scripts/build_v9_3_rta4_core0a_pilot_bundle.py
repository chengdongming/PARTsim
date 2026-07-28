#!/usr/bin/env python3
"""Generate or check the portable RTA4 CORE-0A engineering-pilot freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_core0a_pilot_v2 import (
    PROJECT_ROOT,
    SELECTION_ARTIFACT_PATH,
    build_core0a_selection_v2,
    load_core0a_selection_v2,
    write_canonical_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selection-output", type=Path,
        default=PROJECT_ROOT / SELECTION_ARTIFACT_PATH,
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            document = load_core0a_selection_v2(args.selection_output)
            mode = "check"
        else:
            document = build_core0a_selection_v2()
            write_canonical_json(args.selection_output, document)
            mode = "generate"
        print(json.dumps({
            "mode": mode,
            "selection_output": str(args.selection_output.resolve()),
            "selection_count": len(document["ordered_records"]),
            "core0a_selection_identity": document[
                "core0a_selection_identity"
            ],
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
