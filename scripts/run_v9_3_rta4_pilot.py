#!/usr/bin/env python3
"""Plan or execute reproducible engineering-only RTA4 pilot observations."""

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
from experiments.v9_3.rta4_formal_environment import load_strict_json
from experiments.v9_3.rta4_formal_pilot import (
    RTA4_PILOT_OUTPUT_MARKER, build_pilot_manifest,
    validate_pilot_manifest,
)
from experiments.v9_3.rta4_pilot_execution import (
    PilotExecutionRunner, RTA4_PILOT_EXECUTION_CONFIG,
    validate_pilot_execution_config,
)


def _pairs(
    values: list[str] | None, label: str, *, required: bool,
) -> dict[str, str]:
    result = {}
    for value in values or ():
        if "=" not in value:
            raise ValueError(f"{label} must use CORE=value")
        key, item = value.split("=", 1)
        if key in result:
            raise ValueError(f"duplicate {label}: {key}")
        result[key] = item
    if required and set(result) != set(RTA4_CORES):
        raise ValueError(f"{label} must cover all six cores")
    if result and set(result) != set(RTA4_CORES):
        raise ValueError(f"{label} must be omitted or cover all six cores")
    return result


def _configs(values: list[str] | None) -> tuple[dict[str, Path], dict]:
    pairs = _pairs(values, "config", required=True)
    paths = {
        core: Path(path).resolve(strict=True)
        for core, path in pairs.items()
    }
    return paths, {
        core: load_rta4_formal_config(path, expected_core=core)
        for core, path in paths.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", action="append")
    parser.add_argument("--scale", action="append")
    parser.add_argument("--selection-seed")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--taskset-store", type=Path)
    parser.add_argument("--simulator-binary", type=Path)
    parser.add_argument("--execution-config", type=Path)
    parser.add_argument("--max-records", type=int)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan-only", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--resume", action="store_true")
    modes.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        paths, configs = _configs(args.config)
        root = args.output_root.resolve()
        manifest_path = root / RTA4_PILOT_OUTPUT_MARKER
        create_manifest = args.plan_only or (
            args.execute and not manifest_path.is_file()
        )
        if create_manifest:
            if args.taskset_store is None:
                raise ValueError("--taskset-store is required")
            if args.selection_seed is None:
                raise ValueError("--selection-seed is required")
            scale_pairs = _pairs(args.scale, "scale", required=True)
            scale = {core: int(value) for core, value in scale_pairs.items()}
            if root.exists() and any(root.iterdir()):
                raise ValueError(
                    "new plan/execute requires an empty pilot output root"
                )
            root.mkdir(parents=True, exist_ok=True)
            manifest = build_pilot_manifest(
                configs, core_record_counts=scale,
                selection_seed=args.selection_seed, output_root=root,
                taskset_store=args.taskset_store.resolve(),
                config_paths=paths,
            )
            atomic_write_json(manifest_path, manifest)
        else:
            if args.scale or args.selection_seed:
                raise ValueError(
                    "existing plan/resume/validate-only must not reselect "
                    "pilot records"
                )
            manifest = validate_pilot_manifest(
                load_strict_json(manifest_path), configs,
            )
        if args.plan_only:
            if (
                args.execution_config is not None
                or args.simulator_binary is not None
                or args.max_records is not None
            ):
                raise ValueError(
                    "plan-only does not accept execution-only arguments"
                )
            print(json.dumps({
                "mode": "plan-only",
                "pilot_manifest": str(manifest_path),
                "pilot_manifest_id": manifest["pilot_manifest_id"],
            }, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.execution_config is None:
            raise ValueError(
                "--execution-config is required for execute/resume/validate"
            )
        if args.validate_only and args.max_records is not None:
            raise ValueError(
                "validate-only does not accept --max-records"
            )
        execution_config = validate_pilot_execution_config(
            load_strict_json(args.execution_config), manifest,
            validate_live_source=True,
        )
        if args.taskset_store is not None and str(
            args.taskset_store.resolve()
        ) != execution_config["taskset_store"]:
            raise ValueError("--taskset-store differs from execution config")
        if args.simulator_binary is None:
            raise ValueError(
                "CORE-3 selection requires --simulator-binary"
            )
        simulator = args.simulator_binary.resolve(strict=True)
        if str(simulator) != execution_config[
            "simulator_manifest"
        ]["absolute_path"]:
            raise ValueError(
                "--simulator-binary differs from execution config"
            )
        config_copy = root / RTA4_PILOT_EXECUTION_CONFIG
        if args.execute and not config_copy.exists():
            atomic_write_json(config_copy, execution_config)
        runner = PilotExecutionRunner(configs, manifest, execution_config)
        summary = runner.run(
            resume=args.resume or args.validate_only,
            validate_only=args.validate_only,
            max_records=args.max_records,
        )
        print(json.dumps({
            "mode": (
                "validate-only" if args.validate_only
                else "resume" if args.resume else "execute"
            ),
            "execution_class": summary.execution_class,
            "execution_config_id": summary.execution_config_id,
            "processed_count": summary.processed_count,
            "remaining_count": summary.remaining_count,
            "complete": summary.complete,
            "checkpoint": str(summary.checkpoint_path),
            "audit_id": (
                None if summary.audit is None
                else summary.audit["audit_id"]
            ),
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
