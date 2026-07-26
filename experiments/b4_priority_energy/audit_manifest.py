#!/usr/bin/env python3
"""Stable, result-free auditor for B4-PE I4A manifests."""

import argparse

from manifest_common import ManifestError, audit_records, compact_json, parse_manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = audit_records(parse_manifest(args.manifest))
    except (ManifestError, OSError) as exc:
        print(f"manifest audit failed: {exc}")
        return 1
    if args.json:
        print(compact_json(summary))
    else:
        for key in sorted(summary):
            print(f"{key}: {compact_json(summary[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
