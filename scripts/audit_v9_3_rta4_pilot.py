#!/usr/bin/env python3
"""Independently audit one executed engineering-only RTA4 pilot namespace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.result_writer import atomic_write_json
from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, load_rta4_formal_config,
)
from experiments.v9_3.rta4_pilot_execution import (
    RTA4_PILOT_AUDIT, audit_pilot_namespace,
)


def _configs(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        core, separator, path = value.partition("=")
        if not separator or core in result:
            raise ValueError("--config must use one unique CORE=path")
        result[core] = Path(path).resolve(strict=True)
    if set(result) != set(RTA4_CORES):
        raise ValueError("--config must cover all six cores")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", action="append", required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--output-audit", type=Path)
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="audit an incomplete checkpoint without granting freeze eligibility",
    )
    args = parser.parse_args()
    try:
        paths = _configs(args.config)
        configs = {
            core: load_rta4_formal_config(path, expected_core=core)
            for core, path in paths.items()
        }
        audit = audit_pilot_namespace(
            args.pilot_root, configs,
            require_complete=not args.allow_partial,
        )
        if args.output_audit is not None:
            target = args.output_audit.resolve()
            pilot_root = args.pilot_root.resolve(strict=True)
            try:
                relative = target.relative_to(pilot_root)
            except ValueError:
                relative = None
            if relative is not None and relative.as_posix() != (
                RTA4_PILOT_AUDIT
            ):
                raise ValueError(
                    "audit inside pilot root must use the canonical filename"
                )
            if target.exists():
                raise ValueError("audit output already exists")
            atomic_write_json(target, audit)
        print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
