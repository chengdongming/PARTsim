#!/usr/bin/env python3
"""Execute a validated B4-PE manifest sequentially under the I4B-1 contract."""

import argparse
import sys

import manifest_common as manifest
from execution_common import (
    ExecutionError,
    prepare_and_execute,
    summary_succeeded,
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--simulator-binary", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.execute:
        print("execution requires explicit --execute", file=sys.stderr)
        return 2
    try:
        summary = prepare_and_execute(
            args.manifest,
            args.output_root,
            args.simulator_binary,
            limit=args.limit,
            resume=args.resume,
            retry_failed=args.retry_failed,
        )
    except (ExecutionError, manifest.ManifestError, OSError) as exc:
        print(f"execution failed: {exc}", file=sys.stderr)
        return 1
    print(manifest.compact_json(summary))
    return 0 if summary_succeeded(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
