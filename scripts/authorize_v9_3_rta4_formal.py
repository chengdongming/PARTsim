#!/usr/bin/env python3
"""Build or explicitly confirm one independent RTA4 authorization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.result_writer import atomic_write_json
from experiments.v9_3.rta4_formal_authorization import (
    authorize_candidate, build_authorization_candidate,
)
from experiments.v9_3.rta4_formal_config import RTA4_CORES
from experiments.v9_3.rta4_formal_environment import (
    build_command_manifest, build_dependency_manifest,
    build_environment_manifest, build_hardware_manifest,
    build_simulator_manifest, build_source_manifest,
)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _prepared(directory: Path):
    result = {}
    for core in RTA4_CORES:
        slug = core.lower().replace("-", "")
        result[core] = _json(directory / f"rta4_{slug}_prepared_config.json")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-config", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument(
        "--execution-argv-json", type=Path, required=True,
        help="JSON vector for the exact authorized production command",
    )
    parser.add_argument("--authorization-output", type=Path, required=True)
    parser.add_argument("--source-bindings", type=Path)
    parser.add_argument("--confirm-authorization-id")
    parser.add_argument("--test-authorization", action="store_true")
    args = parser.parse_args()
    try:
        prepared = _json(args.prepared_config)
        all_prepared = _prepared(args.prepared_root)
        freeze = _json(args.freeze_manifest)
        pilot = _json(args.pilot_manifest)
        report = _json(args.pilot_report)
        dependencies = build_dependency_manifest()
        environment = build_environment_manifest(dependencies)
        hardware = build_hardware_manifest()
        operation = "execute"
        command_argv = _json(args.execution_argv_json)
        command = build_command_manifest(
            command_argv, cwd=Path.cwd(), operation=operation,
            core=prepared["core"],
        )
        simulator = build_simulator_manifest(
            prepared["operational"]["simulator_binary"]
        )
        source = build_source_manifest(args.repository_root, args.source)
        candidate = build_authorization_candidate(
            prepared_config=prepared, freeze_manifest=freeze,
            all_prepared_configs=all_prepared, pilot_manifest=pilot,
            pilot_report=report, source_manifest=source,
            dependency_manifest=dependencies, environment_manifest=environment,
            hardware_manifest=hardware, command_manifest=command,
            simulator_manifest=simulator,
            prepared_config_path=args.prepared_config,
            freeze_manifest_path=args.freeze_manifest,
            pilot_manifest_path=args.pilot_manifest,
            pilot_report_path=args.pilot_report,
            authorization_path=args.authorization_output.resolve(),
            source_closure_bindings=(
                {} if args.source_bindings is None else _json(args.source_bindings)
            ),
            test_mode=args.test_authorization,
        )
        output = args.authorization_output.resolve()
        if args.confirm_authorization_id is None:
            atomic_write_json(output, candidate)
            print(json.dumps({
                "authorization_id": candidate["authorization_id"],
                "status": candidate["authorization_status"],
                "candidate_path": str(output),
            }, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        authorized = authorize_candidate(
            candidate,
            confirm_authorization_id=args.confirm_authorization_id,
            test_mode=args.test_authorization,
        )
        atomic_write_json(output, authorized)
        print(json.dumps({
            "authorization_id": authorized["authorization_id"],
            "status": authorized["authorization_status"],
            "authorization_path": str(output),
        }, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
