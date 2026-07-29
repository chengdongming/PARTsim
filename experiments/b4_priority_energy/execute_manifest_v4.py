#!/usr/bin/env python3
"""Preflight or execute the exact authorized B4-PE v4 Pilot manifest."""

import argparse
import sys

import manifest_common as manifest
from execution_common import (
    ExecutionError,
    file_sha256,
    preflight_authorized_v4_pilot_manifest,
    prepare_and_execute,
    summary_succeeded,
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output-root")
    parser.add_argument("--simulator-binary")
    parser.add_argument("--runtime-closure-root")
    parser.add_argument("--runtime-evidence-root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.preflight_only:
        if (
            args.output_root is not None
            or args.simulator_binary is not None
            or args.runtime_closure_root is not None
            or args.runtime_evidence_root is not None
            or args.resume
            or args.retry_failed
        ):
            parser.error(
                "--preflight-only does not accept execution options"
            )
        try:
            records = preflight_authorized_v4_pilot_manifest(args.manifest)
        except (ExecutionError, manifest.ManifestError, OSError) as exc:
            print(f"execution preflight failed: {exc}", file=sys.stderr)
            return 1
        print(
            manifest.compact_json(
                {
                    "authorized_phase": "pilot",
                    "case_count": len(records),
                    "manifest_sha256": file_sha256(args.manifest),
                    "preflight_status": "accepted",
                }
            )
        )
        return 0
    missing = [
        option
        for option, value in (
            ("--output-root", args.output_root),
            ("--simulator-binary", args.simulator_binary),
            ("--runtime-closure-root", args.runtime_closure_root),
            ("--runtime-evidence-root", args.runtime_evidence_root),
        )
        if value is None
    ]
    if missing:
        parser.error(
            "v4 Pilot execution requires " + ", ".join(missing)
        )
    try:
        summary = prepare_and_execute(
            args.manifest,
            args.output_root,
            args.simulator_binary,
            resume=args.resume,
            retry_failed=args.retry_failed,
            runtime_closure_root=args.runtime_closure_root,
            runtime_evidence_root=args.runtime_evidence_root,
        )
    except (ExecutionError, manifest.ManifestError, OSError) as exc:
        print(f"execution failed: {exc}", file=sys.stderr)
        return 1
    print(manifest.compact_json(summary))
    return 0 if summary_succeeded(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
