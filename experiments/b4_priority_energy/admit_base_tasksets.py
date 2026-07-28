#!/usr/bin/env python3
"""Generate and CPU-admit B4-PE v4 base tasksets without starting a campaign."""

import argparse
import hashlib
import sys
from pathlib import Path

import admission_common as admission
import manifest_common as manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--simulator", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    try:
        records = manifest.validate_manifest(manifest_path)
        if {record["schema_version"] for record in records} != {4}:
            raise admission.AdmissionError(
                "admission requires a validated manifest v4 draft"
            )
        inventory = admission.admit_records(
            records,
            Path(args.output_root),
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            Path(args.simulator),
        )
    except (
        OSError,
        manifest.ManifestError,
        admission.AdmissionError,
    ) as exc:
        print(f"base admission failed: {exc}", file=sys.stderr)
        return 1
    print(
        manifest.compact_json(
            {
                "accepted_base_tasksets": len(inventory["base_tasksets"]),
                "campaign_cases_started": 0,
                "inventory":
                    records[0]["base_pool_admission_inventory_relpath"],
                "paper_result": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
