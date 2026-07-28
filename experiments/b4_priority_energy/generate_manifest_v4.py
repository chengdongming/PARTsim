#!/usr/bin/env python3
"""Generate deterministic B4-PE materialization-draft v4 JSON Lines manifests."""

import argparse
from pathlib import Path

from manifest_common import ManifestError, PROTOCOL_V4, render_manifest


def default_output(phase):
    return Path("/tmp") / f"b4_pe_v4_manifest_{phase}.jsonl"


def write_manifest(phase, output=None):
    destination = default_output(phase) if output is None else Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render_manifest(phase, PROTOCOL_V4))
    return destination


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("pilot", "formal_main", "negative_control", "all"),
        required=True,
    )
    parser.add_argument("--output")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        destination = write_manifest(args.phase, args.output)
    except (ManifestError, OSError) as exc:
        print(f"manifest generation failed: {exc}")
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
