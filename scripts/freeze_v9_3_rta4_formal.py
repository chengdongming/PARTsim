#!/usr/bin/env python3
"""Prepare and freeze all six RTA4 formal configurations after pilot."""

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
from experiments.v9_3.rta4_formal_freeze import (
    build_freeze_manifest, prepare_formal_configs,
)
from experiments.v9_3.rta4_formal_environment import load_strict_json


def _json(path: Path):
    return load_strict_json(path)


def _configs(values: list[str]):
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
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-observations", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--pilot-audit", type=Path, required=True)
    parser.add_argument("--timeout-contract", type=Path, required=True)
    parser.add_argument("--operational-contract", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = _configs(args.config)
        configs = {
            core: load_rta4_formal_config(path, expected_core=core)
            for core, path in paths.items()
        }
        prepared = prepare_formal_configs(
            configs,
            pilot_manifest=_json(args.pilot_manifest),
            pilot_observations=_json(args.pilot_observations),
            pilot_report=_json(args.pilot_report),
            pilot_audit=_json(args.pilot_audit),
            timeout_contract=_json(args.timeout_contract),
            operational=_json(args.operational_contract),
            config_paths=paths,
            pilot_root=args.pilot_root,
        )
        freeze = build_freeze_manifest(prepared)
        output = args.output_root.resolve()
        if output.exists() and any(output.iterdir()):
            raise ValueError("freeze output root must be empty")
        output.mkdir(parents=True, exist_ok=True)
        for core in RTA4_CORES:
            slug = core.lower().replace("-", "")
            atomic_write_json(
                output / f"rta4_{slug}_prepared_config.json",
                prepared[core],
            )
        atomic_write_json(output / "rta4_formal_freeze_manifest.json", freeze)
        print(json.dumps({
            "freeze_manifest_id": freeze["freeze_manifest_id"],
            "output_root": str(output),
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
