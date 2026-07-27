#!/usr/bin/env python3
"""Fail-closed validator for B4-PE I4A JSON Lines manifests."""

import argparse

from manifest_common import ManifestError, validate_manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        records = validate_manifest(args.manifest)
    except (ManifestError, OSError) as exc:
        print(f"manifest validation failed: {exc}")
        return 1
    print(f"manifest valid: {len(records)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
