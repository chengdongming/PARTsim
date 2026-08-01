#!/usr/bin/env python3
"""Generate or check a controlled RTA4 V2 or parameterized V3 manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_production_build_manifest import (  # noqa: E402
    generate_production_build_manifest,
    load_and_validate_production_build_manifest,
    write_production_build_manifest,
)
from experiments.v9_3.result_writer import atomic_write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--simulator-binary", type=Path, required=True)
    parser.add_argument("--verifier-binary", type=Path, required=True)
    parser.add_argument("--compiler", default="c++")
    parser.add_argument("--simulator-build-arg", action="append", required=True)
    parser.add_argument("--verifier-build-arg", action="append", required=True)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--profile", choices=("v2-shared-energy", "v3-parameterized"),
        default="v2-shared-energy",
    )
    args = parser.parse_args()
    if args.profile == "v3-parameterized":
        from experiments.v9_3.rta4_production_build_manifest_v3 import (
            generate_production_build_manifest_v3,
            load_production_build_manifest_v3,
        )

        if args.check:
            manifest = load_production_build_manifest_v3(args.output, live=True)
        else:
            keywords = {}
            if args.source:
                keywords["relevant_source_paths"] = tuple(args.source)
            manifest = generate_production_build_manifest_v3(
                source_root=args.source_root,
                simulator_binary=args.simulator_binary,
                verifier_binary=args.verifier_binary,
                compiler=args.compiler,
                build_commands={
                    "simulator": args.simulator_build_arg,
                    "verifier": args.verifier_build_arg,
                },
                **keywords,
            )
            atomic_write_json(args.output, manifest)
    elif args.check:
        manifest = load_and_validate_production_build_manifest(args.output)
    else:
        keywords = {}
        if args.source:
            keywords["relevant_source_paths"] = tuple(args.source)
        manifest = generate_production_build_manifest(
            source_root=args.source_root,
            simulator_binary=args.simulator_binary,
            verifier_binary=args.verifier_binary,
            compiler=args.compiler,
            build_commands={
                "simulator": args.simulator_build_arg,
                "verifier": args.verifier_build_arg,
            },
            **keywords,
        )
        write_production_build_manifest(args.output, manifest)
    print(json.dumps({
        "classification": manifest["classification"],
        "manifest_id": manifest["manifest_id"],
        "formal_authorization": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
