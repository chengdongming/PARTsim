#!/usr/bin/env python3
"""Aggregate only a complete authorized RTA4 formal closure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_aggregation import aggregate_formal_run
from experiments.v9_3.rta4_formal_environment import (
    load_strict_json, validate_command_invocation,
)
from experiments.v9_3.rta4_formal_writer import FORMAL_AUTHORIZATION_EVIDENCE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[])
    args = parser.parse_args()
    try:
        sources = {}
        for value in args.source:
            core, separator, path = value.partition("=")
            if not separator or core in sources:
                raise ValueError("--source must use unique CORE=path")
            sources[core] = Path(path)
        authorization = load_strict_json(
            args.run_root / FORMAL_AUTHORIZATION_EVIDENCE
        )
        validate_command_invocation(
            authorization["command_manifest"], argv=sys.argv,
            cwd=Path.cwd(), operation="aggregate",
            core=authorization["core"],
        )
        manifest = aggregate_formal_run(
            args.run_root, args.output_root,
            source_closures=sources, require_authorized_formal=True,
        )
        print(json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2,
        ))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
