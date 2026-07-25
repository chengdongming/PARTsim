#!/usr/bin/env python3
"""Create deterministic RTA4 pilot selection and engineering-only evidence."""

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
from experiments.v9_3.rta4_formal_pilot import (
    RTA4_PILOT_OUTPUT_MARKER, RTA4_PILOT_REPORT,
    build_pilot_manifest, build_pilot_report,
)


def _pairs(values: list[str], label: str) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use CORE=value")
        key, item = value.split("=", 1)
        if key in result:
            raise ValueError(f"duplicate {label}: {key}")
        result[key] = item
    if set(result) != set(RTA4_CORES):
        raise ValueError(f"{label} must cover all six cores")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", action="append", required=True)
    parser.add_argument("--scale", action="append", required=True)
    parser.add_argument("--selection-seed", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--observations",
        type=Path,
        help="optional JSON observation vector; never accepts scientific results",
    )
    args = parser.parse_args()
    try:
        paths = {core: Path(path).resolve(strict=True) for core, path in _pairs(
            args.config, "config",
        ).items()}
        scale = {core: int(value) for core, value in _pairs(
            args.scale, "scale",
        ).items()}
        configs = {
            core: load_rta4_formal_config(path, expected_core=core)
            for core, path in paths.items()
        }
        root = args.output_root.resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError("pilot output root must be empty")
        root.mkdir(parents=True, exist_ok=True)
        manifest = build_pilot_manifest(
            configs, core_record_counts=scale,
            selection_seed=args.selection_seed, output_root=root,
            config_paths=paths,
        )
        atomic_write_json(root / RTA4_PILOT_OUTPUT_MARKER, manifest)
        result = {"pilot_manifest": str(root / RTA4_PILOT_OUTPUT_MARKER)}
        if args.observations is not None:
            observations = json.loads(
                args.observations.read_text(encoding="utf-8")
            )
            report = build_pilot_report(manifest, observations)
            atomic_write_json(root / RTA4_PILOT_REPORT, report)
            result["pilot_report"] = str(root / RTA4_PILOT_REPORT)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
