#!/usr/bin/env python3
"""Reconstruct and audit one complete engineering-only RTA4 pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.rta4_formal_config import (
    RTA4_CORES, load_rta4_formal_config,
)
from experiments.v9_3.rta4_formal_environment import load_strict_json
from experiments.v9_3.rta4_formal_pilot import (
    validate_pilot_manifest, validate_pilot_report,
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
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = _configs(args.config)
        configs = {
            core: load_rta4_formal_config(path, expected_core=core)
            for core, path in paths.items()
        }
        manifest = validate_pilot_manifest(
            load_strict_json(args.pilot_manifest), configs,
        )
        report = validate_pilot_report(
            load_strict_json(args.pilot_report), manifest,
        )
        print(json.dumps({
            "pilot_manifest_id": manifest["pilot_manifest_id"],
            "pilot_closure_id": report["pilot_closure_id"],
            "pilot_report_id": report["pilot_report_id"],
            "pilot_status": report["pilot_status"],
            "scientific_results_included": False,
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
