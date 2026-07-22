#!/usr/bin/env python3
"""Main CLI for the isolated v9.3 B4 performance experiment."""

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
from experiments.v9_3.performance_config import load_performance_config, plan_counts
from experiments.v9_3.performance_engine import (
    calibration_phase_plan_counts, execute_plan, load_verified_calibration_control,
)
from experiments.v9_3.performance_environment import (
    StageEnvironmentError, assert_environment_compatible, build_stage_environment,
)
from experiments.v9_3.performance_taskset_store import PerformanceTasksetStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan-only", action="store_true")
    modes.add_argument("--freeze-tasksets", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--taskset-store", type=Path)
    parser.add_argument("--simulator-bin", type=Path)
    parser.add_argument("--max-tasksets", type=int)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--calibration-confirmation", action="store_true")
    parser.add_argument(
        "--calibration-phase",
        choices=("initial", "extension-a", "extension-b", "confirmation", "confirmation-full-grid"),
        default="initial",
    )
    args = parser.parse_args()
    config = load_performance_config(args.config)
    phase = args.calibration_phase.replace("-", "_")
    if args.calibration_confirmation:
        if args.calibration_phase != "initial":
            parser.error("do not combine --calibration-confirmation with --calibration-phase")
        phase = "confirmation"
    if phase != "default" and phase != "initial" and config["stage"] != "CALIBRATION":
        parser.error("--calibration-phase requires a CALIBRATION config")
    if phase == "initial":
        phase = "default"
    if args.plan_only:
        counts = plan_counts(config) if phase == "default" else calibration_phase_plan_counts(config, phase)
        print(json.dumps(counts, sort_keys=True))
        return 0
    if args.output_root is not None:
        config["execution"]["output_root"] = str(args.output_root.resolve())
    if args.taskset_store is not None:
        config["execution"]["taskset_store"] = str(args.taskset_store.resolve())
    if args.simulator_bin is not None:
        config["simulation"]["simulator_bin"] = str(args.simulator_bin.resolve())
    store = PerformanceTasksetStore(Path(config["execution"]["taskset_store"]), config)
    if args.freeze_tasksets:
        manifest = store.freeze(max_tasksets_per_utilization=args.max_tasksets)
        print(json.dumps({
            "frozen_tasksets": len(manifest["entries"]),
            "store_identity": manifest["store_identity"],
            "simulator_invoked": False,
        }, sort_keys=True))
        return 0
    if args.execute:
        print(json.dumps(execute_plan(
            config, store, max_requests=args.max_requests, phase=phase,
        ), sort_keys=True))
        return 0
    output_root = Path(config["execution"]["output_root"])
    plan = json.loads((output_root / "formal_plan.json").read_text(encoding="utf-8"))
    results = load_terminal_results(output_root / "terminal_results")
    calibration = load_verified_calibration_control(
        Path(config["execution"]["calibration_seal"]),
    )
    horizon = json.loads(Path(config["execution"]["horizon_seal"]).read_text(encoding="utf-8"))
    try:
        environment = build_stage_environment(config, project_root=PROJECT_ROOT)
        assert_environment_compatible(
            environment, plan["stage_environment"], require_stage_config=True,
        )
        assert_environment_compatible(environment, calibration["stage_environment"])
        assert_environment_compatible(environment, horizon["stage_environment"])
    except (KeyError, StageEnvironmentError) as exc:
        parser.error(f"analyze-only stage environment mismatch: {exc}")
    audit = audit_formal_results(
        plan, results,
        selected_gate_ids=horizon.get("selected_gate_request_ids"),
        unselected_gate_ids=horizon.get("unselected_gate_request_ids"),
        taskset_store_frozen=store.manifest_path.is_file(),
        calibration_source_commit=calibration.get("source_commit"),
        calibration_binary_sha256=calibration.get("simulator_binary_sha256"),
        gate_energy_identity_shared=horizon.get("gate_closure", {}).get(
            "energy_identity_shared_across_horizons"
        ),
        config=config, taskset_store=store,
    )
    summary = generate_four_figures(
        results, audit, output_root=output_root / "analysis",
        bootstrap_seed=config["statistics"]["bootstrap_seed"],
        permutation_seed=config["statistics"]["permutation_seed"],
        resamples=config["statistics"]["resamples"],
    )
    print(json.dumps({"audit": audit, "analysis": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
