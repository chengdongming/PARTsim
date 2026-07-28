#!/usr/bin/env python3
"""Materialize B4-PE v4 inputs only from a CPU-admitted base-pool inventory."""

import argparse
import hashlib
import sys
from pathlib import Path

import manifest_common as manifest
import materialization_common as materialization


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    try:
        records = manifest.validate_manifest(manifest_path)
        if {record["schema_version"] for record in records} != {4}:
            raise materialization.MaterializationError(
                "materializer requires a validated manifest v4 draft"
            )
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        inventory = materialization.materialize_records(
            records,
            Path(args.output_root),
            manifest_sha256=manifest_sha,
        )
    except (
        OSError,
        manifest.ManifestError,
        materialization.MaterializationError,
    ) as exc:
        print(f"materialization failed: {exc}", file=sys.stderr)
        return 1
    result = {
        "base_tasksets": len(inventory["base_tasksets"]),
        "cases": len(inventory["cases"]),
        "execution_tasksets": len(inventory["execution_tasksets"]),
        "inventory": records[0]["materialization_inventory_relpath"],
        "simulator_started": False,
        "sources": len(inventory["sources"]),
        "system_configs": len(inventory["system_configs"]),
    }
    print(manifest.compact_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
