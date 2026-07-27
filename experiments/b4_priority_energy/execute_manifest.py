#!/usr/bin/env python3
"""Execute a validated B4-PE manifest sequentially under the I4B-1 contract."""

import argparse
import sys

import manifest_common as manifest
import integration_smoke_common as integration_smoke
from execution_common import (
    ExecutionError,
    execute_validated_cases,
    prepare_and_execute,
    summary_succeeded,
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest")
    source.add_argument("--integration-smoke-record")
    parser.add_argument("--output-root")
    parser.add_argument("--simulator-binary")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.manifest is not None and (
        args.output_root is None or args.simulator_binary is None
    ):
        parser.error(
            "formal manifest execution requires --output-root and "
            "--simulator-binary"
        )
    if not args.execute:
        print("execution requires explicit --execute", file=sys.stderr)
        return 2
    try:
        if args.manifest is not None:
            summary = prepare_and_execute(
                args.manifest,
                args.output_root,
                args.simulator_binary,
                limit=args.limit,
                resume=args.resume,
                retry_failed=args.retry_failed,
            )
        else:
            if args.output_root is not None or args.simulator_binary is not None:
                raise ExecutionError(
                    "integration-smoke paths come only from the validated record"
                )
            if args.limit is not None:
                raise ExecutionError(
                    "integration-smoke record is already limited to one case"
                )
            envelope = integration_smoke.validate_integration_smoke_record(
                args.integration_smoke_record
            )
            summary = execute_validated_cases(
                envelope["records"],
                envelope["record_path"],
                envelope["output_root"],
                envelope["simulator_path"],
                resume=args.resume,
                retry_failed=args.retry_failed,
            )
    except (
        ExecutionError,
        integration_smoke.IntegrationSmokeError,
        manifest.ManifestError,
        OSError,
    ) as exc:
        print(f"execution failed: {exc}", file=sys.stderr)
        return 1
    print(manifest.compact_json(summary))
    return 0 if summary_succeeded(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
