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
    build_autodl_handoff_v2,
    build_core0a_selection_v2,
    build_portable_candidate_bundle_v2,
    load_strict_canonical_json,
    load_core0a_selection_v2,
    validate_autodl_handoff_v2,
    validate_portable_candidate_bundle_v2,
    write_canonical_json,
)


DEFAULT_BUNDLE_OUTPUT = Path(
    "/tmp/partsim_v9_3_rta4_core0a_candidate_bundle.json"
)
DEFAULT_HANDOFF_OUTPUT = Path(
    "/tmp/partsim_v9_3_rta4_core0a_autodl_handoff.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selection-output", type=Path,
        default=PROJECT_ROOT / SELECTION_ARTIFACT_PATH,
    )
    parser.add_argument("--bundle-output", type=Path, default=DEFAULT_BUNDLE_OUTPUT)
    parser.add_argument("--handoff-output", type=Path, default=DEFAULT_HANDOFF_OUTPUT)
    parser.add_argument(
        "--artifact", choices=("selection", "bundle", "handoff", "all"),
        default="all",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        details = {"mode": "check" if args.check else "generate"}
        if args.check or args.artifact not in {"selection", "all"}:
            document = load_core0a_selection_v2(args.selection_output)
        else:
            document = build_core0a_selection_v2()
            write_canonical_json(args.selection_output, document)
        if args.artifact in {"selection", "all"}:
            details.update({
                "selection_output": str(args.selection_output.resolve()),
                "selection_count": len(document["ordered_records"]),
                "core0a_selection_identity": document[
                    "core0a_selection_identity"
                ],
            })
        bundle = None
        if args.artifact in {"bundle", "handoff", "all"}:
            if args.check:
                bundle = validate_portable_candidate_bundle_v2(
                    load_strict_canonical_json(args.bundle_output),
                )
            else:
                bundle = build_portable_candidate_bundle_v2(
                    selection=document,
                )
                write_canonical_json(args.bundle_output, bundle)
            details.update({
                "bundle_output": str(args.bundle_output.resolve()),
                "portable_freeze_identity": bundle[
                    "portable_freeze_identity"
                ],
            })
        if args.artifact in {"handoff", "all"}:
            assert bundle is not None
            if args.check:
                handoff = validate_autodl_handoff_v2(
                    load_strict_canonical_json(args.handoff_output), bundle,
                )
            else:
                handoff = build_autodl_handoff_v2(bundle)
                write_canonical_json(args.handoff_output, handoff)
            details.update({
                "handoff_output": str(args.handoff_output.resolve()),
                "autodl_handoff_identity": handoff[
                    "autodl_handoff_identity"
                ],
            })
        print(json.dumps(
            details, ensure_ascii=False, sort_keys=True, indent=2,
        ))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
