#!/usr/bin/env python3
"""Safe argv-only preview for B4-PE I4A manifests."""

import argparse
from pathlib import Path

from manifest_common import ManifestError, compact_json, validate_manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output-root", default="/tmp/b4_pe_i4a_outputs")
    parser.add_argument("--execute", action="store_true")
    return parser


def preview_records(records, output_root, limit=None):
    if limit is not None and limit < 0:
        raise ManifestError("limit must be non-negative")
    root = Path(output_root)
    selected = records if limit is None else records[:limit]
    plans = []
    for record in selected:
        replacements = {
            record["taskset_artifact_relpath"]: str(root / record["taskset_artifact_relpath"]),
            record["source_artifact_relpath"]: str(root / record["source_artifact_relpath"]),
            record["system_config_artifact_relpath"]: str(
                root / record["system_config_artifact_relpath"]
            ),
            record["result_relpath"]: str(root / record["result_relpath"]),
        }
        argv = [replacements.get(item, item) for item in record["command_argv"]]
        plans.append(
            {
                "case_id": record["case_id"],
                "command_argv": argv,
                "result_path": replacements[record["result_relpath"]],
            }
        )
    return plans


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.execute:
        print("execution is not implemented in I4A")
        return 2
    try:
        records = validate_manifest(args.manifest)
        plans = preview_records(records, args.output_root, args.limit)
    except (ManifestError, OSError) as exc:
        print(f"manifest dry-run failed: {exc}")
        return 1
    for plan in plans:
        print(compact_json(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
