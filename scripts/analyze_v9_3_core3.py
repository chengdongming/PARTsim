#!/usr/bin/env python3
"""Validate and summarize a bounded CORE-3 V7 local run.

The runner remains the existing V5 runner.  This command only reads terminal
and compressed job-observation artifacts; it never invokes RTA or simulation.
An optional JSON/JSONL RTA result file enables the empirical soundness check.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.v9_3.rta4_core3_artifacts_v6 import (
    artifact_binding_from_row_v1,
    load_bound_gzip_json_v1,
    strict_json_file_v6,
)
from experiments.v9_3.rta4_core3_contracts_v7 import (
    CORE3_RESULT_DOMAIN_V7,
    CORE3_RESULT_SCHEMA_V7,
    CORE3_SIMULATION_CONTRACT_V7,
)
from experiments.v9_3.rta4_formal_config import domain_hash
from experiments.v9_3.rta4_local_execution_v5 import RTA4_LOCAL_RESULT_DOMAIN_V7


class Core3V7AnalysisError(ValueError):
    """Raised when a CORE-3 V7 result set is incomplete or inconsistent."""


def _fraction(value: Any, label: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise Core3V7AnalysisError(f"{label} is not an exact rational") from exc
    if result < 0:
        raise Core3V7AnalysisError(f"{label} must be nonnegative")
    return result


def _load_jobs(root: Path, row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    binding = artifact_binding_from_row_v1(row, "job_observations")
    if binding is None:
        raise Core3V7AnalysisError("V7 result has no job-observation artifact")
    value = load_bound_gzip_json_v1(root, binding, reject_unbound_raw=False)
    if not isinstance(value, Mapping) or not isinstance(value.get("job_observations"), list):
        raise Core3V7AnalysisError("job-observation artifact is malformed")
    jobs = value["job_observations"]
    if len(jobs) != row.get("job_observation_count"):
        raise Core3V7AnalysisError("job-observation count mismatch")
    return [job for job in jobs if isinstance(job, Mapping)]


def _validate_terminal(root: Path, path: Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    row = strict_json_file_v6(path)
    if not isinstance(row, Mapping) or row.get("row_schema") != "ASAP_BLOCK_V9_3_RTA4_LOCAL_RESULT_V7":
        raise Core3V7AnalysisError(f"not a CORE-3 V7 terminal: {path}")
    execution = row.get("execution_identity")
    if path.stem != execution or row.get("not_for_paper") is not True:
        raise Core3V7AnalysisError(f"terminal identity/classification mismatch: {path}")
    unsigned = dict(row)
    observed_identity = unsigned.pop("result_identity", None)
    if observed_identity != domain_hash(RTA4_LOCAL_RESULT_DOMAIN_V7, unsigned):
        raise Core3V7AnalysisError(f"local terminal identity mismatch: {path}")
    worker_result = row.get("result")
    simulation = worker_result.get("result") if isinstance(worker_result, Mapping) else None
    if not isinstance(simulation, Mapping) or simulation.get("result_schema_version") != CORE3_RESULT_SCHEMA_V7:
        raise Core3V7AnalysisError(f"CORE-3 V7 terminal/result schema mismatch: {path}")
    material = dict(simulation)
    simulation_identity = material.pop("simulation_result_identity", None)
    if simulation_identity != domain_hash(CORE3_RESULT_DOMAIN_V7, material):
        raise Core3V7AnalysisError(f"simulation result identity mismatch: {path}")
    if simulation.get("simulation_status") != "COMPLETED":
        raise Core3V7AnalysisError(f"simulation is not complete: {path}")
    unit = simulation.get("model_energy_unit_joules")
    if unit != "1/1000" or simulation.get("simulation_tick_ms") != 1:
        raise Core3V7AnalysisError(f"V7 energy/tick contract mismatch: {path}")
    capacity = _fraction(simulation["battery_capacity_model_units"], "capacity model")
    if _fraction(simulation["battery_capacity_j"], "capacity physical") != capacity / 1000:
        raise Core3V7AnalysisError(f"capacity projection mismatch: {path}")
    initial = _fraction(simulation["physical_initial_energy_model_units"], "initial model")
    if _fraction(simulation["physical_initial_energy_j"], "initial physical") != initial / 1000:
        raise Core3V7AnalysisError(f"initial-energy projection mismatch: {path}")
    for model, physical in zip(simulation["projection_e0_model_units"], simulation["projection_e0_j"]):
        if _fraction(physical, "E0 physical") != _fraction(model, "E0 model") / 1000:
            raise Core3V7AnalysisError(f"E0 projection mismatch: {path}")
    for task in simulation.get("per_task_energy_projection", ()):
        if not task.get("observed_energy_validated") or not task.get("observed_energy_within_tolerance"):
            raise Core3V7AnalysisError(f"task-energy validation failed: {path}")
        if _fraction(task["expected_physical_energy_j_per_tick"], "task physical energy") != _fraction(task["model_energy_per_tick"], "task model energy") / 1000:
            raise Core3V7AnalysisError(f"task-energy projection mismatch: {path}")
    for field in ("harvested_energy_j", "consumed_energy_j", "overflow_energy_j", "battery_min_j", "battery_max_j"):
        _fraction(simulation[field], field)
    return simulation, _load_jobs(root, row)


def _load_rta_rows(path: Path) -> list[Mapping[str, Any]]:
    if path.suffix == ".jsonl":
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        values = json.loads(path.read_text())
    if isinstance(values, Mapping):
        values = values.get("results", [])
    if not isinstance(values, list) or not all(isinstance(row, Mapping) for row in values):
        raise Core3V7AnalysisError("RTA result input must be a JSON list or JSONL")
    return list(values)


def analyze_root(root: Path | str, *, rta_results: Path | str | None = None) -> dict[str, Any]:
    root = Path(root).expanduser().resolve(strict=True)
    paths = sorted((root / "local_terminal_results_v5").glob("*.json"))
    if not paths:
        raise Core3V7AnalysisError("no CORE-3 V7 terminals found")
    simulations = []
    seen: set[str] = set()
    energy_blocked_ticks = 0
    deadline_miss_jobs = 0
    for path in paths:
        simulation, jobs = _validate_terminal(root, path)
        identity = str(simulation["simulation_result_identity"])
        if identity in seen:
            raise Core3V7AnalysisError("duplicate simulation result identity")
        seen.add(identity)
        energy_blocked_ticks += sum(int(job.get("energy_blocked_ticks", 0)) for job in jobs)
        deadline_miss_jobs += sum(bool(job.get("deadline_miss")) for job in jobs)
        simulations.append((simulation, jobs))
    violations = []
    if rta_results is None:
        soundness = "NOT_RUN_NO_RTA_INPUT"
    else:
        soundness = "PASS"
        for rta in _load_rta_rows(Path(rta_results).resolve(strict=True)):
            if not rta.get("rta_proven") and rta.get("taskset_proven") is not True:
                continue
            taskset = str(rta.get("taskset_identity"))
            task_id = str(rta.get("task_id"))
            for simulation, jobs in simulations:
                if str(simulation.get("taskset_identity", "")) != taskset:
                    continue
                for job in jobs:
                    if str(job.get("task_id")) == task_id and job.get("deadline_miss") is True:
                        soundness = "FAIL"
                        violations.append({"taskset_identity": taskset, "task_id": task_id, "type": "CERTIFIED_JOB_DEADLINE_MISS"})
    return {
        "core": "CORE-3",
        "contract_version": CORE3_SIMULATION_CONTRACT_V7,
        "n_results": len(simulations),
        "duplicate_results": 0,
        "internal_error": 0,
        "timeout": 0,
        "deadline_miss_jobs": deadline_miss_jobs,
        "energy_blocked_ticks": energy_blocked_ticks,
        "energy_sanity": "PASS",
        "soundness_check": soundness,
        "soundness_violations": violations,
        "resume_checked_by_runner": (root / "local_checkpoint_v5.json").is_file(),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--rta-results", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        print(json.dumps(analyze_root(args.run_root, rta_results=args.rta_results), sort_keys=True, indent=2))
    except (Core3V7AnalysisError, OSError, json.JSONDecodeError) as exc:
        print(f"CORE3 V7 analysis failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Core3V7AnalysisError", "analyze_root", "main"]
