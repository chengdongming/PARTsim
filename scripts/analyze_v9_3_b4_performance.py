#!/usr/bin/env python3
"""Analyze a complete B4 terminal result set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.performance_analysis import generate_four_figures
from experiments.v9_3.performance_audit import audit_formal_results, load_terminal_results
from experiments.v9_3.performance_config import load_performance_config
from experiments.v9_3.performance_environment import (
    StageEnvironmentError, assert_environment_compatible, build_stage_environment,
)
from experiments.v9_3.performance_taskset_store import PerformanceTasksetStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration-seal", type=Path, required=True)
    parser.add_argument("--horizon-seal", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--simulator-bin", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    results = load_terminal_results(args.results)
    calibration = json.loads(args.calibration_seal.read_text(encoding="utf-8"))
    horizon = json.loads(args.horizon_seal.read_text(encoding="utf-8"))
    config = load_performance_config(args.config)
    if config["stage"] != "FORMAL":
        parser.error("analysis requires a FORMAL config")
    if args.simulator_bin is not None:
        config["simulation"]["simulator_bin"] = str(args.simulator_bin.resolve())
    try:
        environment = build_stage_environment(config, project_root=PROJECT_ROOT)
        assert_environment_compatible(
            environment, plan["stage_environment"], require_stage_config=True,
        )
        assert_environment_compatible(environment, calibration["stage_environment"])
        assert_environment_compatible(environment, horizon["stage_environment"])
    except (KeyError, StageEnvironmentError) as exc:
        parser.error(f"analysis stage environment mismatch: {exc}")
    audit = audit_formal_results(
        plan, results,
        selected_gate_ids=horizon.get("selected_gate_request_ids"),
        unselected_gate_ids=horizon.get("unselected_gate_request_ids"),
        calibration_source_commit=calibration.get("source_commit"),
        calibration_binary_sha256=calibration.get("simulator_binary_sha256"),
        gate_energy_identity_shared=horizon.get("gate_closure", {}).get(
            "energy_identity_shared_across_horizons"
        ),
        config=config,
        taskset_store=PerformanceTasksetStore(
            Path(config["execution"]["taskset_store"]), config,
        ),
    )
    summary = generate_four_figures(results, audit, output_root=args.output)
    print(json.dumps({"audit": audit, "analysis": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
