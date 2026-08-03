#!/usr/bin/env python3
"""Preflight or explicitly execute a local, non-paper B4-PE V5 campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from .manifest_v5 import (
        B4ManifestV5Error,
        execute_local_campaign_v5,
        preflight_campaign_v5,
    )
except ImportError:  # direct execution from the repository root
    from manifest_v5 import (
        B4ManifestV5Error,
        execute_local_campaign_v5,
        preflight_campaign_v5,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate and describe only; never starts the simulator",
    )
    mode.add_argument(
        "--execute-local",
        action="store_true",
        help="execute through the existing state machine as non-paper evidence",
    )
    parser.add_argument("--acknowledge-not-for-paper", action="store_true")
    parser.add_argument("--output-root")
    parser.add_argument("--simulator-binary")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true", default=None)
    parser.add_argument("--retry-failed", action="store_true", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute_local and not args.acknowledge_not_for_paper:
        print(
            "B4-PE V5 local execution requires "
            "--acknowledge-not-for-paper"
        )
        return 2
    try:
        preview = (
            preflight_campaign_v5(args.config)
            if args.preflight_only
            else execute_local_campaign_v5(
                args.config,
                acknowledge_not_for_paper=args.acknowledge_not_for_paper,
                output_root=args.output_root,
                simulator_binary=args.simulator_binary,
                limit=args.limit,
                resume=args.resume,
                retry_failed=args.retry_failed,
            )
        )
    except (B4ManifestV5Error, OSError) as exc:
        print(f"B4-PE V5 operation failed: {exc}")
        return 1
    print(json.dumps(preview, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
