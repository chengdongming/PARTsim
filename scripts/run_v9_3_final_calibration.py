#!/usr/bin/env python3
"""Run the single bounded constrained-deadline v9.3 grid calibration."""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3.aggregation import aggregate_core2
from experiments.v9_3.calibration import (
    CalibrationError,
    diagnose_instance,
    load_instances,
    stage_b_cells,
    summarize_cells,
)
from experiments.v9_3.config import config_hash, dump_config, fraction_text, load_config
from experiments.v9_3.execution_engine import ExecutionEngine
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS,
    atomic_write_json,
    read_csv,
    write_csv,
    write_file_hashes,
)


EXPECTED_E0 = {
    "1",
    "21473099401200000281/200000000000000000000",
}


def _dynamic_csv(path: Path, rows: Sequence[Mapping[str, Any]], *, fallback=()) -> None:
    columns = list(fallback)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    write_csv(path, columns, rows)


def _phase_rows(root: Path, table: str, phase: str) -> list[Dict[str, Any]]:
    return [{"phase": phase, **row} for row in read_csv(root / table)]


def _run_phase(config: Mapping[str, Any], *, resume: bool):
    engine = ExecutionEngine(config)
    outcome = engine.run(resume=resume)
    if outcome.stopped or outcome.terminal != outcome.requested:
        raise CalibrationError(
            f"{config['experiment_id']} stopped or has missing terminals"
        )
    aggregate_core2(outcome.output_root)
    if engine.service is None:
        raise CalibrationError("execution engine did not retain service curve")
    return outcome, engine.service.values


def _diagnose(run_root: Path, phase: str, beta, timeout: float):
    responses, access, closures = [], [], []
    for instance in load_instances(run_root, phase):
        new_response, new_access, new_closure = diagnose_instance(
            instance, beta, timeout_seconds=timeout
        )
        responses.extend(new_response)
        access.extend(new_access)
        closures.extend(new_closure)
    return responses, access, closures


def _validate_calibration(
    result_rows: Sequence[Mapping[str, Any]],
    generated_rows: Sequence[Mapping[str, Any]],
    tightness_rows: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    analysis_ids = [row["analysis_id"] for row in result_rows]
    if len(analysis_ids) != len(set(analysis_ids)):
        raise CalibrationError("duplicate final analysis result")
    if any(
        fraction_text(Fraction(str(row["exact_e0"]))) != str(row["exact_e0"])
        for row in result_rows
    ):
        raise CalibrationError("E0 round-trip failure")
    observed_e0 = {fraction_text(Fraction(str(row["exact_e0"]))) for row in result_rows}
    if observed_e0 != EXPECTED_E0:
        raise CalibrationError(f"unexpected calibration E0 grid: {observed_e0}")
    if any(row["dominance_invariant_status"] == "DOMINANCE_INVARIANT_VIOLATION" for row in result_rows):
        raise CalibrationError("production dominance violation")
    if any(row["source_vector_hash"] != row["target_carry_in_vector_hash"] for row in result_rows if row["analysis_variant"] == "LOC_THETA_CW" and row["source_vector_hash"]):
        raise CalibrationError("source/target carry-in vector hash mismatch")
    if any(row["response_relation"] == "VIOLATION" for row in tightness_rows):
        raise CalibrationError("diagnostic response dominance violation")
    instances = {
        (row["phase"], row["cell_id"], row["taskset_id"])
        for row in result_rows
    }
    for key in instances:
        variants = {
            row["analysis_variant"] for row in result_rows
            if (row["phase"], row["cell_id"], row["taskset_id"]) == key
        }
        if variants != {"CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"}:
            raise CalibrationError("missing calibration variant")
    taskset_hash = {row["taskset_id"]: row["taskset_hash"] for row in generated_rows}
    paired: Dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in result_rows:
        key = (row["phase"], row["utilization"], row["taskset_id"])
        paired[key].add(row["taskset_hash"])
        if row["taskset_hash"] != taskset_hash[row["taskset_id"]]:
            raise CalibrationError("taskset result/store hash mismatch")
    if any(len(values) != 1 for values in paired.values()):
        raise CalibrationError("E0 pairing hash mismatch")
    if len(instances) > 12 or len(result_rows) > 60:
        raise CalibrationError("calibration hard size bound exceeded")
    return len(instances), len(result_rows)


def _eligible_utilizations(
    cell_summary: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    candidates = []
    for utilization in sorted({str(row["utilization"]) for row in cell_summary}, key=Fraction):
        cells = [row for row in cell_summary if str(row["utilization"]) == utilization]
        relevant = [row for row in result_rows if str(row["utilization"]) == utilization]
        timeout_rate = sum(row["solver_status"] == "TIMEOUT" for row in relevant) / len(relevant)
        safe = bool(
            all(int(row["dominance_violation_count"]) == 0 for row in cells)
            and all(int(row["numeric_failure"]) == 0 and int(row["internal_failure"]) == 0 for row in cells)
            and timeout_rate <= .10
            and any(row["taskset_proven"] == "True" for row in relevant)
        )
        if safe:
            candidates.append(utilization)
    if not candidates:
        raise CalibrationError("no utilization satisfies the mandatory formal-grid rule")
    return candidates


def _runtime_estimate(
    result_rows: Sequence[Mapping[str, Any]],
    utilizations: Sequence[str],
    variants: Sequence[str],
    tasksets_per_cell: int,
    worker_count: int,
) -> Dict[str, Any]:
    selected = [
        row for row in result_rows
        if str(row["utilization"]) in utilizations and row["analysis_variant"] in variants
    ]
    by_variant: Dict[str, list[float]] = defaultdict(list)
    for row in selected:
        by_variant[row["analysis_variant"]].append(float(row["runtime_wall_seconds"]))
    cells = len(utilizations) * len(EXPECTED_E0)
    analysis_count = cells * tasksets_per_cell * len(variants)
    expected_solver = 0.0
    conservative_solver = 0.0
    variant_runtime = {}
    for variant in variants:
        values = sorted(by_variant[variant])
        if not values:
            raise CalibrationError(f"no runtime observations for {variant}")
        mean_value = sum(values) / len(values)
        p95_value = values[max(0, math.ceil(.95 * len(values)) - 1)]
        count = cells * tasksets_per_cell
        expected_solver += mean_value * count
        conservative_solver += p95_value * count
        variant_runtime[variant] = {"mean_seconds": mean_value, "p95_seconds": p95_value}
    overhead = 1.15
    return {
        "analysis_count": analysis_count,
        "worker_count": worker_count,
        "variant_runtime": variant_runtime,
        "expected_solver_seconds": expected_solver,
        "conservative_solver_seconds": conservative_solver,
        "expected_wall_seconds": expected_solver / worker_count * overhead,
        "conservative_wall_seconds": conservative_solver / worker_count * overhead,
    }


def _candidate_config(
    source: Mapping[str, Any], *, core_name: str, utilizations: Sequence[str],
    tasksets_per_cell: int, timeout: float, retry: float, base_seed: int,
    workers: int,
) -> Dict[str, Any]:
    config = deepcopy(dict(source))
    config["parameter_status"] = "PROPOSED_NOT_YET_FROZEN"
    config["experiment_id"] = (
        f"asap-block-v9.3-{core_name.lower()}-formal-candidate-"
        "workload-contract-v2"
    )
    config["core"] = core_name
    config.pop("calibration", None)
    config["analysis"]["variants"] = (
        ["CW_THETA_CW", "LOC_THETA_LOC"] if core_name == "CORE-1" else
        ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"]
    )
    config["analysis"]["timeout_seconds"] = timeout
    config["analysis"]["retry_timeout_seconds"] = retry
    config["analysis"]["retry_policy"] = "timeout_once"
    config["analysis"]["worker_count"] = workers
    config["grid"]["utilization_points"] = list(utilizations)
    config["grid"]["tasksets_per_cell"] = tasksets_per_cell
    config["grid"]["base_seed"] = base_seed
    config["grid"]["seed_mode"] = "generation_dimensions"
    config["grid"]["taskset_index_start"] = 0
    config["grid"].pop("cell_filter", None)
    suffix = "core1" if core_name == "CORE-1" else "core2"
    config["execution"]["output_root"] = (
        f"artifacts/v9_3_{suffix}_formal_workload_contract_v2"
    )
    config["execution"]["taskset_store"] = (
        "artifacts/v9_3_formal_taskset_store_workload_contract_v2"
    )
    config["execution"]["resume"] = False
    return config


def _report(
    instances: int, analyses: int, stage_a_summary, selected_b,
    final_summary, proposal,
) -> str:
    lines = [
        "# ASAP-BLOCK v9.3 final constrained-deadline calibration",
        "",
        "This is the final bounded parameter calibration, not a formal paper run.",
        "No further Pilot or parameter search is authorized by this report.",
        "",
        f"- Actual taskset/E0 instances: {instances}",
        f"- Actual production analyses: {analyses}",
        f"- Stage B selected cells: {len(selected_b)}",
        "- Production timeout: 30 seconds; TIMEOUT-only retry: 60 seconds",
        "",
        "## Stage A classification",
        "",
        "| U | exact E0 | class | strict envelope | local-only closure | strict response | certification gain | timeout |",
        "|---:|---:|:---:|---:|---:|---:|---:|---:|",
    ]
    for row in stage_a_summary:
        lines.append(
            f"| {row['utilization']} | {row['exact_e0']} | {row['category']} | "
            f"{row['strict_envelope_count']} | {row['local_only_closure_count']} | "
            f"{row['strict_response_count']} | {row['certification_gain_count']} | {row['timeout']} |"
        )
    lines.extend(["", "## Final cell metrics", ""])
    lines.append("| U | exact E0 | instances | class | certified/requested | runtime p95 (s) | D/T min/median/max | dominance violations |")
    lines.append("|---:|---:|---:|:---:|---:|---:|---|---:|")
    for row in final_summary:
        lines.append(
            f"| {row['utilization']} | {row['exact_e0']} | {row['taskset_instances']} | "
            f"{row['category']} | {row['certified']}/{row['requested']} | "
            f"{row['runtime_p95']:.6f} | {row['d_over_t_min']} / "
            f"{row['d_over_t_median']} / {row['d_over_t_max']} | "
            f"{row['dominance_violation_count']} |"
        )
    lines.extend([
        "", "## Proposed formal grid", "",
        f"- Utilizations: {proposal['utilization_points']}",
        f"- Exact E0: {proposal['initial_energy_values']}",
        f"- Tasksets per cell: {proposal['tasksets_per_cell']}",
        f"- Timeout/retry: {proposal['timeout_seconds']} / {proposal['retry_timeout_seconds']} seconds",
        f"- Formal base seed: {proposal['base_seed']}",
        f"- Worker count: {proposal['worker_count']}",
        f"- CORE-1 analyses: {proposal['core1']['analysis_count']}",
        f"- CORE-2 analyses: {proposal['core2']['analysis_count']}",
        f"- Expected combined wall time: {proposal['combined_expected_wall_seconds'] / 3600:.3f} hours",
        f"- Conservative combined wall time: {proposal['combined_conservative_wall_seconds'] / 3600:.3f} hours",
        "", "Parameter status: PROPOSED_NOT_YET_FROZEN.",
        "The candidate configurations must be reviewed/frozen before any formal run starts.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config, expected_core="CORE-2")
    calibration = config.get("calibration")
    if not isinstance(calibration, dict):
        raise CalibrationError("missing calibration configuration")
    root = Path(calibration["artifact_root"])
    root.mkdir(parents=True, exist_ok=True)
    calibration_config_path = root / "calibration_config.yaml"
    if calibration_config_path.exists():
        existing = load_config(calibration_config_path, expected_core="CORE-2")
        if config_hash(existing) != config_hash(config):
            raise CalibrationError("calibration config hash mismatch")
    else:
        dump_config(config, calibration_config_path)

    phase_a = deepcopy(config)
    phase_a["execution"]["output_root"] = str(root / "phase_a")
    phase_a["execution"]["taskset_store"] = str(root / "taskset_store")
    phase_a["grid"]["taskset_index_start"] = 0
    phase_a["grid"]["tasksets_per_cell"] = 1
    phase_a["grid"].pop("cell_filter", None)
    outcome_a, beta_a = _run_phase(phase_a, resume=args.resume)
    diagnostic_timeout = float(calibration.get("diagnostic_timeout_seconds", 60))
    tight_a, access_a, closure_a = _diagnose(
        outcome_a.output_root, "A", beta_a, diagnostic_timeout
    )
    result_a = _phase_rows(outcome_a.output_root, "per_taskset_results.csv", "A")
    generated_a = _phase_rows(outcome_a.output_root, "generated_tasksets.csv", "A")
    for row in generated_a:
        row["utilization"] = fraction_text(
            Fraction(row["target_total_utilization"]) / int(row["M"])
        )
    stage_a_summary = summarize_cells(result_a, generated_a, tight_a)
    selected_b = stage_b_cells(stage_a_summary)

    result_b: list[Dict[str, Any]] = []
    generated_b: list[Dict[str, Any]] = []
    tight_b: list[Dict[str, Any]] = []
    access_b: list[Dict[str, Any]] = []
    closure_b: list[Dict[str, Any]] = []
    outcome_b = None
    if selected_b:
        phase_b = deepcopy(config)
        phase_b["experiment_id"] = f"{config['experiment_id']}-stage-b"
        phase_b["execution"]["output_root"] = str(root / "phase_b")
        phase_b["execution"]["taskset_store"] = str(root / "taskset_store")
        phase_b["grid"]["taskset_index_start"] = 1
        phase_b["grid"]["tasksets_per_cell"] = 1
        phase_b["grid"]["cell_filter"] = selected_b
        outcome_b, beta_b = _run_phase(phase_b, resume=args.resume)
        tight_b, access_b, closure_b = _diagnose(
            outcome_b.output_root, "B", beta_b, diagnostic_timeout
        )
        result_b = _phase_rows(outcome_b.output_root, "per_taskset_results.csv", "B")
        generated_b = _phase_rows(outcome_b.output_root, "generated_tasksets.csv", "B")
        for row in generated_b:
            row["utilization"] = fraction_text(
                Fraction(row["target_total_utilization"]) / int(row["M"])
            )

    result_rows = result_a + result_b
    generated_rows = generated_a + generated_b
    tightness_rows = tight_a + tight_b
    access_rows = access_a + access_b
    closure_rows = closure_a + closure_b
    instances, analyses = _validate_calibration(
        result_rows, generated_rows, tightness_rows
    )
    final_summary = summarize_cells(result_rows, generated_rows, tightness_rows)
    eligible_u = _eligible_utilizations(final_summary, result_rows)
    all_runtimes = [float(row["runtime_wall_seconds"]) for row in result_rows]
    runtime_p95 = sorted(all_runtimes)[max(0, math.ceil(.95 * len(all_runtimes)) - 1)]
    timeout_count = sum(row["solver_status"] == "TIMEOUT" for row in result_rows)
    tasksets_per_cell = 100 if timeout_count == 0 and runtime_p95 <= 5 else 50 if runtime_p95 <= 15 else 30
    worker_count = min(4, os.cpu_count() or 1)
    formal_seed = int(calibration["proposed_formal_base_seed"])
    if formal_seed == int(config["grid"]["base_seed"]):
        raise CalibrationError("formal base seed reuses calibration seed")
    core1 = _runtime_estimate(
        result_rows, eligible_u, ["CW_THETA_CW", "LOC_THETA_LOC"],
        tasksets_per_cell, worker_count,
    )
    core2 = _runtime_estimate(
        result_rows, eligible_u,
        ["CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"],
        tasksets_per_cell, worker_count,
    )
    proposal = {
        "parameter_status": "PROPOSED_NOT_YET_FROZEN",
        "utilization_points": eligible_u,
        "initial_energy_values": list(config["energy"]["initial_energy_values"]),
        "tasksets_per_cell": tasksets_per_cell,
        "timeout_seconds": 30,
        "retry_timeout_seconds": 60,
        "worker_count": worker_count,
        "base_seed": formal_seed,
        "core1": core1, "core2": core2,
        "combined_analysis_count": core1["analysis_count"] + core2["analysis_count"],
        "combined_expected_wall_seconds": core1["expected_wall_seconds"] + core2["expected_wall_seconds"],
        "combined_conservative_wall_seconds": core1["conservative_wall_seconds"] + core2["conservative_wall_seconds"],
        "calibration_seed_reused": False,
        "formal_run_started": False,
    }
    candidate_core1 = _candidate_config(
        config, core_name="CORE-1", utilizations=eligible_u,
        tasksets_per_cell=tasksets_per_cell, timeout=30, retry=60,
        base_seed=formal_seed, workers=worker_count,
    )
    candidate_core2 = _candidate_config(
        config, core_name="CORE-2", utilizations=eligible_u,
        tasksets_per_cell=tasksets_per_cell, timeout=30, retry=60,
        base_seed=formal_seed, workers=worker_count,
    )
    dump_config(candidate_core1, ROOT / "configs/v9_3_core1_formal_candidate.yaml")
    dump_config(candidate_core2, ROOT / "configs/v9_3_core2_formal_candidate.yaml")

    timeout_attempts = _phase_rows(outcome_a.output_root, "analysis_attempts.csv", "A")
    if outcome_b is not None:
        timeout_attempts.extend(_phase_rows(outcome_b.output_root, "analysis_attempts.csv", "B"))
    timeout_attempts = [row for row in timeout_attempts if row["solver_status"] == "TIMEOUT"]
    _dynamic_csv(root / "generated_tasksets.csv", generated_rows, fallback=("phase",))
    _dynamic_csv(root / "results.csv", result_rows, fallback=("phase",))
    _dynamic_csv(root / "tightness.csv", tightness_rows, fallback=("phase",))
    _dynamic_csv(root / "timeout_attempts.csv", timeout_attempts, fallback=("phase", *ATTEMPT_COLUMNS))
    _dynamic_csv(root / "envelope_access_points.csv", access_rows, fallback=("phase",))
    _dynamic_csv(root / "local_only_closures.csv", closure_rows, fallback=("phase",))
    summary = {
        "schema": "ASAP_BLOCK_V9_3_FINAL_CALIBRATION_V1",
        "parameter_status": "PROPOSED_NOT_YET_FROZEN",
        "formal_run_started": False,
        "actual_instances": instances,
        "actual_analyses": analyses,
        "stage_a_instances": 6,
        "stage_a_analyses": 30,
        "stage_a_cell_summary": stage_a_summary,
        "stage_b_selected_cells": selected_b,
        "stage_b_instances": len(selected_b),
        "stage_b_analyses": len(selected_b) * 5,
        "final_cell_summary": final_summary,
        "strict_envelope_count": sum(row["strict_envelope_count"] for row in final_summary),
        "local_only_closure_count": sum(row["local_only_closure_count"] for row in final_summary),
        "strict_response_count": sum(row["strict_response_count"] for row in final_summary),
        "certification_gain_count": sum(row["certification_gain_count"] for row in final_summary),
        "timeout_attempt_count": len(timeout_attempts),
        "dominance_violation_count": sum(row["dominance_violation_count"] for row in final_summary),
        "proposed_formal_grid": proposal,
    }
    atomic_write_json(root / "calibration_summary.json", summary)
    atomic_write_json(root / "proposed_formal_grid.json", proposal)
    (root / "calibration_report.md").write_text(
        _report(instances, analyses, stage_a_summary, selected_b, final_summary, proposal),
        encoding="utf-8",
    )
    write_file_hashes(root)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
