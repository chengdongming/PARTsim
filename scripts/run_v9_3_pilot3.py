#!/usr/bin/env python3
"""Run ASAP-BLOCK v9.3 Pilot-3 paired exact-E0 one-dimensional scan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asap_block_rta as legacy_rta
import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
import asap_block_v9_3_runner as production_runner
from scripts import run_v9_3_pilot as pilot1
from scripts import run_v9_3_pilot2 as pilot2


VARIANTS = production_runner.VARIANT_ORDER
INTERVAL_COLUMNS = (
    "interval_id", "relation", "taskset_id", "U_norm", "source_E0", "task_id",
    "priority_rank", "w", "h", "q", "complete_envelope", "local_envelope",
    "service_value_without_e0", "lower_bound_inclusive", "upper_bound_exclusive",
    "midpoint",
)
CANDIDATE_COLUMNS = (
    "candidate_id", "exact_e0", "numerator", "denominator", "origins",
    "covered_intervals", "covered_tasksets", "covered_tasks", "covered_relations",
    "covered_variants", "covered_U_0_4_intervals", "covered_U_0_4_tasksets",
    "covered_U_0_6_intervals", "covered_U_0_6_tasksets", "representation_cost",
    "minimum_distance_to_selected", "selected", "selection_rank",
)
SELECTED_COLUMNS = (
    "e0_index", "selection_role", "selection_rank", "exact_e0", "numerator",
    "denominator", "origins", "covered_intervals", "covered_tasksets",
    "covered_tasks", "covered_relations", "covered_variants",
    "covered_U_0_4_intervals", "covered_U_0_4_tasksets",
    "covered_U_0_6_intervals", "covered_U_0_6_tasksets",
)
PAIRED_TASKSET_COLUMNS = (
    "phase", "U_norm", "U_norm_index", "taskset_index", "generation_seed",
    "taskset_id", "target_total_utilization", "actual_total_utilization",
    "task_count", "distinct_power_count", "taskset_semantic_hash",
    "priority_rank_hash", "power_vector_hash", "generation_runtime_seconds",
    "task_input_json",
)
RESULT_COLUMNS = (
    "phase", "cell_id", "U_norm", "taskset_index", "generation_seed",
    "taskset_id", "exact_e0", "e0_index", "request_id", "analysis_id",
    "analysis_variant", "initial_solver_status", "retry_solver_status",
    "final_solver_status", "certification_status", "taskset_proven",
    "first_failed_priority", "candidate_vector", "task_status_vector",
    "candidate_found_task_count", "certified_task_count", "checked_w_count",
    "checked_h_count", "checked_q_count", "envelope_calls",
    "worker_startup_seconds", "solver_wall_seconds", "solver_cpu_seconds",
    "serialization_seconds", "deserialization_seconds",
    "transport_and_exit_seconds", "total_wall_seconds", "timeout",
    "numeric_error", "internal_error", "not_applicable", "dependency_status",
    "dominance_status", "source_vector_hash", "target_carry_in_vector_hash",
    "task_input_hash",
)
ATTEMPT_COLUMNS = (
    "phase", "cell_id", "taskset_id", "generation_seed", "U_norm", "exact_e0",
    "analysis_variant", "analysis_id", "attempt_index", "attempt_budget_seconds",
    "solver_status", "certification_status", "candidate_vector",
    "task_status_vector", "worker_startup_seconds", "solver_wall_seconds",
    "solver_cpu_seconds", "serialization_seconds", "deserialization_seconds",
    "transport_and_exit_seconds", "total_wall_seconds", "outer_timeout",
    "error_code",
)
ACCESS_COLUMNS = (
    "phase", "cell_id", "relation", "taskset_id", "U_norm", "exact_e0",
    "task_id", "priority_rank", "carry_in_vector_hash", "w", "h", "q",
    "q_plus_h_equals_w", "visited_by_complete", "visited_by_local",
    "complete_envelope", "local_envelope", "envelope_relation",
    "service_value", "complete_energy_satisfied", "local_energy_satisfied",
    "complete_closure", "local_closure", "local_only_closure",
    "predicted_interval_hit", "complete_candidate", "local_candidate",
    "response_relation",
)
CLOSURE_COLUMNS = (
    "phase", "cell_id", "relation", "taskset_id", "U_norm", "exact_e0",
    "task_id", "priority_rank", "w", "h", "q", "complete_candidate",
    "local_candidate", "earlier_than_complete_candidate",
    "earlier_than_local_candidate", "candidate_changed",
)
RESPONSE_COLUMNS = (
    "phase", "cell_id", "relation", "taskset_id", "U_norm", "exact_e0",
    "task_id", "priority_rank", "complete_solver_status",
    "local_solver_status", "complete_certification_status",
    "local_certification_status", "complete_candidate", "local_candidate",
    "response_relation", "improvement", "certification_gain",
    "access_diagnostic_status", "complete_diagnostic_solver_status",
    "local_diagnostic_solver_status",
)
FAILURE_COLUMNS = (
    "severity", "stage", "taskset_id", "analysis_variant", "code", "detail",
    "input_file",
)
ADAPTIVE_DECISION_COLUMNS = (
    "exact_e0", "gate", "U_norm", "tasksets_completed", "analyses_completed",
    "local_only_closures", "strict_response_tasks", "certification_gain_tasks",
    "decision",
)


class Pilot3Error(RuntimeError):
    """Fail-closed Pilot-3 error."""


def _fraction_text(value: Optional[Fraction]) -> Optional[str]:
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _runtime(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise Pilot3Error("runtime must be finite and non-negative")
    return format(value, ".9f")


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _append_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    values = list(rows)
    if not values:
        return
    write_header = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="raise", lineterminator="\n")
        if write_header:
            writer.writeheader()
        for row in values:
            writer.writerow({column: row.get(column) for column in columns})


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict) or config.get("pilot_version") != 3:
        raise Pilot3Error("Pilot-3 config must be a version-3 mapping")
    if config.get("rta_version") != "v9.3" or str(config.get("contract_version")) != "1.3.12":
        raise Pilot3Error("Pilot-3 requires v9.3 and v1.3.12")
    if config["analysis"]["variants"] != [variant.name for variant in VARIANTS]:
        raise Pilot3Error("five variants or their production order changed")
    screening = config["screening"]
    frozen = {
        "task_n": 10, "M": 4, "deadline_mode": "implicit",
        "power_mode": "generator_default_heterogeneous",
        "normalized_utilizations": [0.4, 0.6], "tasksets_per_utilization": 5,
        "base_seed": 930312,
    }
    for key, expected in frozen.items():
        if screening.get(key) != expected:
            raise Pilot3Error(f"screening.{key} must remain {expected!r}")
    if config["timeout"] != {"initial_seconds": 60, "retry_seconds": 90, "max_workers": 1}:
        raise Pilot3Error("Pilot-3 timeout policy must be single-worker 60 then conditional 90")
    if config["confirmation"]["base_seed"] != 930412:
        raise Pilot3Error("confirmation base seed changed")
    if config["analysis"]["numerical_mode"] != "EXACT_RATIONAL":
        raise Pilot3Error("Pilot-3 requires exact rational mode")
    if config.get("diagnostics") != {
        "replay_seconds_per_task": 60,
        "retry_diagnostic_timeout": False,
        "skip_production_timeout_tasks": True,
    }:
        raise Pilot3Error("Pilot-3 diagnostic replay policy changed")
    if Fraction(str(config["e0_selection"]["minimum_exact_separation"])) <= 0:
        raise Pilot3Error("minimum exact E0 separation must be positive")
    return config


def _pilot2_context(config: Mapping[str, Any]) -> Dict[str, Any]:
    saved = PROJECT_ROOT / str(config["pilot2"]["artifact_root"]) / "pilot2_config.yaml"
    pilot2_config = pilot2.load_config(saved)
    return pilot2._baseline_context(pilot2_config)


def reconstruct_intervals(
    config: Mapping[str, Any], context: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Rebuild all Pilot-2 strict E0 separation intervals as Fractions."""

    context = context or _pilot2_context(config)
    path = PROJECT_ROOT / str(config["pilot2"]["artifact_root"]) / "baseline_tightness_diagnostics.csv"
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for source in csv.DictReader(handle):
            if source["record_type"] != "ACCESS_POINT" or source["envelope_relation"] != "STRICT":
                continue
            service = context["beta"][int(source["h"]) + int(source["q"]) - 1]
            complete = Fraction(source["complete_envelope"])
            local = Fraction(source["local_envelope"])
            lower = max(Fraction(0), local - service)
            upper = complete - service
            if lower >= upper or upper <= 0:
                continue
            midpoint = (lower + upper) / 2
            rows.append({
                "interval_id": f"I{len(rows):04d}", "relation": source["relation"],
                "taskset_id": source["taskset_id"], "U_norm": source["U_norm"],
                "source_E0": source["E0"], "task_id": source["task_id"],
                "priority_rank": int(source["priority_rank"]), "w": int(source["w"]),
                "h": int(source["h"]), "q": int(source["q"]),
                "complete_envelope": _fraction_text(complete),
                "local_envelope": _fraction_text(local),
                "service_value_without_e0": _fraction_text(service),
                "lower_bound_inclusive": _fraction_text(lower),
                "upper_bound_exclusive": _fraction_text(upper),
                "midpoint": _fraction_text(midpoint),
                "_lower": lower, "_upper": upper, "_midpoint": midpoint,
            })
    expected = int(config["pilot2"]["expected_separation_intervals"])
    if len(rows) != expected:
        raise Pilot3Error(f"strict interval reconstruction found {len(rows)}, expected {expected}")
    return rows


def _quantile_member(values: Sequence[Fraction], numerator: int, denominator: int) -> Fraction:
    if not values:
        raise Pilot3Error("cannot take a quantile of an empty exact sequence")
    index = (numerator * (len(values) - 1) + denominator // 2) // denominator
    return values[index]


def _coverage(candidate: Fraction, intervals: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    hits = [row for row in intervals if row["_lower"] <= candidate < row["_upper"]]
    by_u = defaultdict(int)
    tasksets_by_u: Dict[str, set] = defaultdict(set)
    for row in hits:
        by_u[str(row["U_norm"])] += 1
        tasksets_by_u[str(row["U_norm"])].add(str(row["taskset_id"]))
    variants_by_relation = {
        "DEADLINE_CARRY_IN": {"CW_D", "LOC_D"},
        "FIXED_CW_CARRY_IN": {"CW_THETA_CW", "LOC_THETA_CW"},
    }
    covered_variants = set()
    for relation in {str(row["relation"]) for row in hits}:
        covered_variants.update(variants_by_relation.get(relation, set()))
    return {
        "covered_intervals": len(hits),
        "covered_tasksets": len({str(row["taskset_id"]) for row in hits}),
        "covered_tasks": len({(str(row["taskset_id"]), str(row["task_id"])) for row in hits}),
        "covered_relations": len({str(row["relation"]) for row in hits}),
        "covered_variants": len(covered_variants),
        "covered_U_0_4_intervals": by_u["0.4"],
        "covered_U_0_4_tasksets": len(tasksets_by_u["0.4"]),
        "covered_U_0_6_intervals": by_u["0.6"],
        "covered_U_0_6_tasksets": len(tasksets_by_u["0.6"]),
    }


def rank_and_select_e0(
    intervals: Sequence[Mapping[str, Any]], config: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build and greedily select the predeclared all-exact E0 candidates."""

    origins: Dict[Fraction, set] = defaultdict(set)
    for row in intervals:
        origins[row["_midpoint"]].add("INTERVAL_MIDPOINT")
    endpoints = sorted({row["_lower"] for row in intervals} | {row["_upper"] for row in intervals})
    for left, right in zip(endpoints, endpoints[1:]):
        origins[(left + right) / 2].add("ADJACENT_ENDPOINT_REPRESENTATIVE")
    midpoints = sorted(row["_midpoint"] for row in intervals)
    for label, numerator, denominator in (("P05", 5, 100), ("MEDIAN", 1, 2), ("P95", 95, 100)):
        center = _quantile_member(midpoints, numerator, denominator)
        position = midpoints.index(center)
        origins[center].add(label)
        if position:
            origins[midpoints[position - 1]].add(f"{label}_LOWER_NEIGHBOR")
        if position + 1 < len(midpoints):
            origins[midpoints[position + 1]].add(f"{label}_UPPER_NEIGHBOR")
    for control in config["e0_selection"]["controls"]:
        origins[Fraction(str(control))].add("CONTROL")

    candidates = []
    for value, value_origins in origins.items():
        coverage = _coverage(value, intervals)
        candidates.append({
            "candidate_id": "", "exact_e0": _fraction_text(value),
            "numerator": value.numerator, "denominator": value.denominator,
            "origins": ";".join(sorted(value_origins)), **coverage,
            "representation_cost": len(str(abs(value.numerator))) + len(str(value.denominator)),
            "minimum_distance_to_selected": None, "selected": False,
            "selection_rank": None, "_value": value,
        })
    candidates.sort(key=lambda row: (
        -row["covered_tasksets"], -row["covered_tasks"], -row["covered_intervals"],
        -row["covered_variants"], row["representation_cost"], row["_value"],
    ))
    minimum = Fraction(str(config["e0_selection"]["minimum_exact_separation"]))
    limit = int(config["e0_selection"]["max_data_values"])
    selected_data: List[Dict[str, Any]] = []
    for row in candidates:
        if "CONTROL" in row["origins"].split(";"):
            continue
        distances = [abs(row["_value"] - chosen["_value"]) for chosen in selected_data]
        if distances and min(distances) < minimum:
            continue
        row["minimum_distance_to_selected"] = _fraction_text(min(distances)) if distances else None
        row["selected"] = True
        row["selection_rank"] = len(selected_data) + 1
        selected_data.append(row)
        if len(selected_data) == limit:
            break
    if len(selected_data) != limit:
        raise Pilot3Error("exact candidate ranking could not select five separated data values")
    controls = [row for row in candidates if "CONTROL" in row["origins"].split(";")]
    if len(controls) != 2:
        raise Pilot3Error("exact E0 controls must be zero and one")
    for row in controls:
        row["selected"] = True
        row["selection_rank"] = 0
    candidates.sort(key=lambda row: row["_value"])
    for index, row in enumerate(candidates):
        row["candidate_id"] = f"E0C{index:05d}"
    selected_raw = sorted(controls + selected_data, key=lambda row: row["_value"])
    selected = []
    for e0_index, row in enumerate(selected_raw):
        selected.append({
            "e0_index": e0_index,
            "selection_role": "CONTROL" if "CONTROL" in row["origins"].split(";") else "DATA",
            "selection_rank": row["selection_rank"], "exact_e0": row["exact_e0"],
            "numerator": row["numerator"], "denominator": row["denominator"],
            "origins": row["origins"],
            **{column: row[column] for column in (
                "covered_intervals", "covered_tasksets", "covered_tasks",
                "covered_relations", "covered_variants", "covered_U_0_4_intervals",
                "covered_U_0_4_tasksets", "covered_U_0_6_intervals",
                "covered_U_0_6_tasksets",
            )},
            "_value": row["_value"],
        })
    return candidates, selected


def derive_paired_seed(base_seed: int, utilization_index: int, taskset_index: int) -> int:
    """Derive a generator seed which intentionally has no E0 component."""

    material = f"{base_seed}|{utilization_index}|{taskset_index}"
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % 2147483647


def _analysis_context(config: Mapping[str, Any], output_root: Path) -> Dict[str, Any]:
    adapter = {
        "task_generation": {
            "M": config["screening"]["M"],
            "task_p_max": config["screening"]["generation"]["task_p_max"],
        },
        "energy_model": dict(config["energy_model"]),
    }
    with tempfile.TemporaryDirectory(prefix="v9_3_pilot3_system_") as temp:
        system_path = pilot1._prepare_system_config(adapter, Path(temp))
        beta, beta_hash, beta_spec = pilot1._build_exact_service_curve(system_path, adapter)
        saved_system = output_root / "pilot3_system_config.yaml"
        shutil.copyfile(system_path, saved_system)
    return {
        "beta": beta, "beta_hash": beta_hash, "beta_spec": beta_spec,
        "config_hash": pilot1._sha256_file(output_root / "pilot3_config.yaml"),
        "system_path": saved_system,
    }


def _generate_paired_taskset(
    config: Mapping[str, Any], context: Mapping[str, Any], u_norm: Any,
    u_index: int, taskset_index: int, base_seed: int, phase: str,
) -> pilot1.GeneratedTaskset:
    screening = config["screening"]
    generation = screening["generation"]
    seed = derive_paired_seed(base_seed, u_index, taskset_index)
    target = Fraction(str(u_norm)) * int(screening["M"])
    with tempfile.TemporaryDirectory(prefix="v9_3_pilot3_generation_") as temp:
        task_path = Path(temp) / "tasks.yaml"
        command = [
            sys.executable, str(pilot1.TASK_GENERATOR), "-n", str(screening["task_n"]),
            "-u", format(float(target), ".15g"), "-p", str(generation["task_p_min"]),
            "-P", str(generation["task_p_max"]), "-c", str(screening["M"]),
            "--seed", str(seed), "-s", str(context["system_path"]), "-o", str(task_path),
            "--min-task-util", str(generation["min_task_util"]),
            "--max-task-util", str(generation["max_task_util"]),
            "--wcet-rounding", str(generation["wcet_rounding"]),
            "--actual-utilization-tolerance-total", str(generation["actual_utilization_tolerance_total"]),
        ]
        started = time.perf_counter()
        completed = subprocess.run(
            command, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=60, check=False,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
            raise Pilot3Error(f"task generation failed with code {completed.returncode}: {detail}")
        legacy_tasks = legacy_rta.rm_order(legacy_rta.load_tasks(str(task_path)))
        document = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    raw_by_name = {str(item["name"]): item for item in document["taskset"]}
    system = legacy_rta.load_system_config(str(context["system_path"]))
    tasks = []
    payload = []
    for rank, legacy_task in enumerate(legacy_tasks):
        raw = raw_by_name[legacy_task.name]
        power = Fraction(str(system.task_energy_per_tick(legacy_task.workload)))
        task_id = str(rank)
        tasks.append(core.V93Task(task_id, legacy_task.wcet, legacy_task.deadline, legacy_task.period, power))
        payload.append({
            "task_id": task_id, "source_name": legacy_task.name, "priority_rank": rank,
            "C": legacy_task.wcet, "D": legacy_task.deadline, "T": legacy_task.period,
            "P": _fraction_text(power), "workload": pilot1._task_workload(raw),
            "arrival_offset": int(next((
                part.split("=", 1)[1] for part in str(raw.get("params", "")).split(",")
                if part.strip().startswith("arrival_offset=")
            ), "0")),
        })
    if any(item.deadline != item.period for item in tasks):
        raise Pilot3Error("paired Pilot-3 generator produced D != T")
    actual = sum(Fraction(item.wcet, item.period) for item in tasks)
    if abs(actual - target) > Fraction(str(generation["actual_utilization_tolerance_total"])):
        raise Pilot3Error("paired taskset utilization is outside tolerance")
    semantic = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:TASKSET_SEMANTIC:v9.3",
        {"M": screening["M"], "tasks": payload, "service_curve_hash": context["beta_hash"]},
    )
    priority_hash = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:PRIORITY:v9.3",
        [{"task_id": item["task_id"], "priority_rank": item["priority_rank"]} for item in payload],
    )
    power_hash = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:POWER_VECTOR:v9.3",
        [{"task_id": item["task_id"], "P": item["P"]} for item in payload],
    )
    taskset_id = f"v93-p3-{phase.lower()}-u{u_index}-t{taskset_index:02d}-{semantic[:12]}"
    return pilot1.GeneratedTaskset(
        seed, taskset_id, format(float(u_norm), ".15g"), u_index, 0, 0,
        taskset_index, target, actual, semantic, priority_hash, power_hash,
        tuple(tasks), tuple(payload), elapsed,
    )


def _paired_taskset_row(phase: str, generated: pilot1.GeneratedTaskset) -> Dict[str, Any]:
    return {
        "phase": phase, "U_norm": generated.u_norm, "U_norm_index": generated.u_norm_index,
        "taskset_index": generated.taskset_index, "generation_seed": generated.seed,
        "taskset_id": generated.taskset_id,
        "target_total_utilization": _fraction_text(generated.target_total_utilization),
        "actual_total_utilization": _fraction_text(generated.actual_total_utilization),
        "task_count": len(generated.tasks),
        "distinct_power_count": len({item.power for item in generated.tasks}),
        "taskset_semantic_hash": generated.semantic_hash,
        "priority_rank_hash": generated.priority_hash, "power_vector_hash": generated.power_hash,
        "generation_runtime_seconds": _runtime(generated.generation_runtime_seconds),
        "task_input_json": json.dumps(list(generated.task_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _reconstruct_paired_taskset(row: Mapping[str, str]) -> pilot1.GeneratedTaskset:
    payload = tuple(json.loads(row["task_input_json"]))
    tasks = tuple(
        core.V93Task(
            str(item["task_id"]), int(item["C"]), int(item["D"]), int(item["T"]),
            Fraction(str(item["P"])),
        )
        for item in payload
    )
    return pilot1.GeneratedTaskset(
        int(row["generation_seed"]), row["taskset_id"], row["U_norm"],
        int(row["U_norm_index"]), 0, 0, int(row["taskset_index"]),
        Fraction(row["target_total_utilization"]),
        Fraction(row["actual_total_utilization"]), row["taskset_semantic_hash"],
        row["priority_rank_hash"], row["power_vector_hash"], tasks, payload,
        float(row["generation_runtime_seconds"]),
    )


def _run_request(
    generated: pilot1.GeneratedTaskset, variant: taskset.AnalysisVariant,
    budget: int, context: Mapping[str, Any], analysis_id: str,
    source: Optional[taskset.TasksetAnalysisResult] = None,
) -> pilot2.TimedExecution:
    inp = pilot1._analysis_input(
        generated, context["beta"], context["beta_hash"], context["config_hash"], budget
    )
    dependency = taskset.DependencyVectorCheckStatus.NOT_CHECKED
    if variant is taskset.AnalysisVariant.LOC_THETA_CW:
        dependency = (
            taskset.DependencyVectorCheckStatus.VALID
            if source is not None and production_runner._source_is_jointly_certified(source)
            else taskset.DependencyVectorCheckStatus.INVALID
        )
    request = production_runner.V93DispatchRequest(
        analysis_id, variant, inp, source=source,
        dependency_check_status=dependency, configuration_timeout_seconds=budget,
    )
    execution = pilot2.execute_analysis(request, budget)
    if execution.outer_timeout:
        raise Pilot3Error("hard outer timeout exceeded solver budget plus grace")
    if execution.result is None:
        raise Pilot3Error(f"analysis worker error: {execution.error_code}: {execution.error_detail}")
    pilot1.validate_analysis_result(execution.result, generated, source)
    if execution.result.n_tasks_certified not in {0, execution.result.n_tasks_total}:
        raise Pilot3Error("P0 partial taskset certification state")
    return execution


def _attempt_row(
    phase: str, cell_id: str, generated: pilot1.GeneratedTaskset, e0: Fraction,
    variant: taskset.AnalysisVariant, analysis_id: str, attempt_index: int,
    budget: int, execution: pilot2.TimedExecution,
) -> Dict[str, Any]:
    if execution.result is None:
        raise Pilot3Error("attempt row requires a result")
    result = execution.result
    candidates, statuses = pilot2._result_vectors(result)
    return {
        "phase": phase, "cell_id": cell_id, "taskset_id": generated.taskset_id,
        "generation_seed": generated.seed, "U_norm": generated.u_norm,
        "exact_e0": _fraction_text(e0), "analysis_variant": variant.name,
        "analysis_id": analysis_id, "attempt_index": attempt_index,
        "attempt_budget_seconds": budget, "solver_status": result.solver_status.value,
        "certification_status": result.certification_status.value,
        "candidate_vector": candidates, "task_status_vector": statuses,
        "worker_startup_seconds": _runtime(execution.worker_startup_seconds),
        "solver_wall_seconds": _runtime(execution.solver_wall_seconds),
        "solver_cpu_seconds": _runtime(execution.solver_cpu_seconds),
        "serialization_seconds": _runtime(execution.serialization_seconds),
        "deserialization_seconds": _runtime(execution.deserialization_seconds),
        "transport_and_exit_seconds": _runtime(execution.transport_and_exit_seconds),
        "total_wall_seconds": _runtime(execution.total_wall_seconds),
        "outer_timeout": execution.outer_timeout, "error_code": execution.error_code,
    }


def _result_row(
    phase: str, cell_id: str, generated: pilot1.GeneratedTaskset, e0: Fraction,
    e0_index: int, variant: taskset.AnalysisVariant,
    attempts: Sequence[pilot2.TimedExecution],
) -> Dict[str, Any]:
    result = attempts[-1].result
    if result is None:
        raise Pilot3Error("result row requires a final result")
    candidates, statuses = pilot2._result_vectors(result)
    counters = pilot2._counter_totals(result)
    source_hash = pilot1._hash_vector(result.source_candidate_vector)
    task_input_hash = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:PAIRED_INPUT:v9.3", generated.task_payload
    )
    return {
        "phase": phase, "cell_id": cell_id, "U_norm": generated.u_norm,
        "taskset_index": generated.taskset_index, "generation_seed": generated.seed,
        "taskset_id": generated.taskset_id, "exact_e0": _fraction_text(e0),
        "e0_index": e0_index,
        "request_id": pilot1._domain_hash(
            "ASAP_BLOCK:PILOT3:REQUEST:v9.3", {"analysis_id": result.analysis_id}
        ),
        "analysis_id": result.analysis_id, "analysis_variant": variant.name,
        "initial_solver_status": attempts[0].result.solver_status.value,
        "retry_solver_status": result.solver_status.value if len(attempts) == 2 else None,
        "final_solver_status": result.solver_status.value,
        "certification_status": result.certification_status.value,
        "taskset_proven": result.taskset_proven,
        "first_failed_priority": result.first_failed_priority,
        "candidate_vector": candidates, "task_status_vector": statuses,
        "candidate_found_task_count": result.n_tasks_candidate_found,
        "certified_task_count": result.n_tasks_certified,
        "checked_w_count": counters[0], "checked_h_count": counters[1],
        "checked_q_count": counters[2], "envelope_calls": counters[3],
        "worker_startup_seconds": _runtime(attempts[-1].worker_startup_seconds),
        "solver_wall_seconds": _runtime(attempts[-1].solver_wall_seconds),
        "solver_cpu_seconds": _runtime(attempts[-1].solver_cpu_seconds),
        "serialization_seconds": _runtime(attempts[-1].serialization_seconds),
        "deserialization_seconds": _runtime(attempts[-1].deserialization_seconds),
        "transport_and_exit_seconds": _runtime(attempts[-1].transport_and_exit_seconds),
        "total_wall_seconds": _runtime(attempts[-1].total_wall_seconds),
        "timeout": result.solver_status is taskset.AnalysisSolverStatus.TIMEOUT,
        "numeric_error": result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR,
        "internal_error": result.solver_status is taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        "not_applicable": result.solver_status is taskset.AnalysisSolverStatus.NOT_APPLICABLE_DEPENDENCY,
        "dependency_status": result.dependency_check_status.value,
        "dominance_status": result.dominance_invariant_status.value,
        "source_vector_hash": source_hash,
        "target_carry_in_vector_hash": source_hash if variant is taskset.AnalysisVariant.LOC_THETA_CW else None,
        "task_input_hash": task_input_hash,
    }


def _run_with_timeout_retry(
    phase: str, cell_id: str, generated: pilot1.GeneratedTaskset, e0: Fraction,
    variant: taskset.AnalysisVariant, context: Mapping[str, Any],
    source: Optional[taskset.TasksetAnalysisResult], attempt_rows: List[Dict[str, Any]],
    attempt_path: Path,
) -> List[pilot2.TimedExecution]:
    analysis_id = pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:ANALYSIS:v9.3",
        {
            "phase": phase, "taskset_id": generated.taskset_id,
            "exact_e0": _fraction_text(e0), "variant": variant.name,
        },
    )
    executions = []
    for attempt_index, budget in enumerate((60, 90), start=1):
        execution = _run_request(generated, variant, budget, context, analysis_id, source=source)
        executions.append(execution)
        attempt_rows.append(_attempt_row(
            phase, cell_id, generated, e0, variant, analysis_id,
            attempt_index, budget, execution,
        ))
        _write_csv(attempt_path, ATTEMPT_COLUMNS, attempt_rows)
        if execution.result.solver_status is not taskset.AnalysisSolverStatus.TIMEOUT:
            break
    final = executions[-1].result
    if final.solver_status in {
        taskset.AnalysisSolverStatus.NUMERIC_ERROR,
        taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
    }:
        raise Pilot3Error(
            f"P0 {final.solver_status.value} in {generated.taskset_id} "
            f"E0={_fraction_text(e0)} {variant.name}"
        )
    return executions


def _analysis_id(
    phase: str, taskset_id: str, e0: Fraction, variant: taskset.AnalysisVariant,
) -> str:
    return pilot1._domain_hash(
        "ASAP_BLOCK:PILOT3:ANALYSIS:v9.3",
        {
            "phase": phase, "taskset_id": taskset_id,
            "exact_e0": _fraction_text(e0), "variant": variant.name,
        },
    )


def _run_five(
    phase: str, generated_base: pilot1.GeneratedTaskset, e0: Fraction,
    e0_index: int, context: Mapping[str, Any], attempt_rows: List[Dict[str, Any]],
    attempt_path: Path,
) -> Tuple[Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult], List[Dict[str, Any]]]:
    generated = replace(generated_base, e0=e0, e0_index=e0_index)
    cell_id = f"U{generated.u_norm}-E0={_fraction_text(e0)}"
    results: Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult] = {}
    rows = []
    for variant in VARIANTS:
        source = results.get(taskset.AnalysisVariant.CW_THETA_CW) if (
            variant is taskset.AnalysisVariant.LOC_THETA_CW
        ) else None
        executions = _run_with_timeout_retry(
            phase, cell_id, generated, e0, variant, context, source,
            attempt_rows, attempt_path,
        )
        final = executions[-1].result
        if final is None:
            raise Pilot3Error("missing final production result")
        results[variant] = final
        rows.append(_result_row(phase, cell_id, generated, e0, e0_index, variant, executions))
    if tuple(results) != VARIANTS:
        raise Pilot3Error("five production variants are missing or reordered")
    pilot1.dominance_rows(generated, results)
    return results, rows


def _run_five_adaptive(
    generated_base: pilot1.GeneratedTaskset, e0: Fraction, e0_index: int,
    context: Mapping[str, Any], attempt_rows: List[Dict[str, Any]],
    attempt_path: Path,
) -> Tuple[Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult], List[Dict[str, Any]]]:
    """Run one adaptive instance, resuming one preserved 60-second timeout if present."""

    phase = "SCREENING"
    generated = replace(generated_base, e0=e0, e0_index=e0_index)
    cell_id = f"U{generated.u_norm}-E0={_fraction_text(e0)}"
    results: Dict[taskset.AnalysisVariant, taskset.TasksetAnalysisResult] = {}
    rows = []
    for variant in VARIANTS:
        analysis_id = _analysis_id(phase, generated.taskset_id, e0, variant)
        prior = [row for row in attempt_rows if str(row["analysis_id"]) == analysis_id]
        source = results.get(taskset.AnalysisVariant.CW_THETA_CW) if (
            variant is taskset.AnalysisVariant.LOC_THETA_CW
        ) else None
        if prior:
            if not (
                len(prior) == 1
                and int(prior[0]["attempt_budget_seconds"]) == 60
                and prior[0]["solver_status"] == "TIMEOUT"
            ):
                raise Pilot3Error(
                    "adaptive resume supports only the preserved single 60-second TIMEOUT"
                )
            execution = _run_request(
                generated, variant, 90, context, analysis_id, source=source
            )
            attempt_rows.append(_attempt_row(
                phase, cell_id, generated, e0, variant, analysis_id, 2, 90, execution
            ))
            _write_csv(attempt_path, ATTEMPT_COLUMNS, attempt_rows)
            executions = [execution]
            row = _result_row(
                phase, cell_id, generated, e0, e0_index, variant, executions
            )
            row["initial_solver_status"] = "TIMEOUT"
            row["retry_solver_status"] = execution.result.solver_status.value
        else:
            executions = _run_with_timeout_retry(
                phase, cell_id, generated, e0, variant, context, source,
                attempt_rows, attempt_path,
            )
            row = _result_row(
                phase, cell_id, generated, e0, e0_index, variant, executions
            )
        final = executions[-1].result
        if final is None:
            raise Pilot3Error("missing adaptive final production result")
        if final.solver_status in {
            taskset.AnalysisSolverStatus.NUMERIC_ERROR,
            taskset.AnalysisSolverStatus.INTERNAL_CONFORMANCE_FAILURE,
        }:
            raise Pilot3Error("P0 numeric/internal failure during adaptive screening")
        results[variant] = final
        rows.append(row)
    pilot1.dominance_rows(generated, results)
    return results, rows


def _record_evaluated(record: taskset.TaskAnalysisRecord) -> bool:
    return record.solver_status not in {
        taskset.TaskSolverStatus.NOT_EVALUATED_AFTER_PREFIX_FAILURE,
        taskset.TaskSolverStatus.NOT_APPLICABLE_DEPENDENCY,
    }


def _trace_once(
    kind: core.EnvelopeKind, generated: pilot1.GeneratedTaskset, rank: int,
    carry: Mapping[str, int], beta: Sequence[Fraction], timeout: int,
    envelope_cache: Dict[Tuple[Any, ...], Fraction],
) -> Tuple[core.V93SearchResult, Dict[Tuple[int, int, int], Mapping[str, object]], set]:
    trace: Dict[Tuple[int, int, int], Mapping[str, object]] = {}
    closures = set()

    def observer(event: Mapping[str, object]) -> None:
        if event.get("event_type") != "Q_CHECK":
            return
        key = (int(event["w"]), int(event["h"]), int(event["q"]))
        trace[key] = dict(event)
        if event.get("h_result") == "CLOSED":
            closures.add(key)

    carry_key = tuple(sorted((str(name), int(value)) for name, value in carry.items()))

    def cached_envelope(**kwargs: Any) -> Fraction:
        cache_key = (
            kwargs["kind"].value, rank, carry_key, int(kwargs["w"]),
            int(kwargs["h"]), int(kwargs["q"]),
        )
        if cache_key not in envelope_cache:
            envelope_cache[cache_key] = core.exact_energy_envelope_v9_3(**kwargs)
        return envelope_cache[cache_key]

    result = core.canonical_closure_search_v9_3(
        kind, generated.tasks[rank], generated.tasks[:rank], generated.tasks[rank + 1:],
        4, carry, Fraction(generated.e0), beta, timeout_seconds=timeout,
        envelope_function=cached_envelope, trace_observer=observer,
    )
    return result, trace, closures


def _trace_diagnostic(
    kind: core.EnvelopeKind, generated: pilot1.GeneratedTaskset, rank: int,
    carry: Mapping[str, int], beta: Sequence[Fraction],
    envelope_cache: Dict[Tuple[Any, ...], Fraction],
) -> Tuple[core.V93SearchResult, Dict[Tuple[int, int, int], Mapping[str, object]], set]:
    return _trace_once(kind, generated, rank, carry, beta, 60, envelope_cache)


def _response_relation(
    complete_candidate: Optional[int], local_candidate: Optional[int],
) -> Tuple[str, Optional[int]]:
    if complete_candidate is not None and local_candidate is not None:
        difference = complete_candidate - local_candidate
        if difference > 0:
            return "TIGHTER", difference
        if difference == 0:
            return "EQUAL", 0
        return "VIOLATION", difference
    if complete_candidate is None and local_candidate is not None:
        return "LOCAL_ONLY_CANDIDATE", None
    if complete_candidate is not None:
        return "COMPLETE_ONLY_CANDIDATE", None
    return "NO_COMMON_CANDIDATE", None


def _diagnose_pair_task(
    phase: str, cell_id: str, relation: str, generated: pilot1.GeneratedTaskset,
    rank: int, carry: Mapping[str, int], complete_record: taskset.TaskAnalysisRecord,
    local_record: taskset.TaskAnalysisRecord, beta: Sequence[Fraction],
    envelope_cache: Dict[Tuple[Any, ...], Fraction],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, str]]:
    complete, complete_trace, complete_closures = _trace_diagnostic(
        core.EnvelopeKind.COMPLETE, generated, rank, carry, beta, envelope_cache
    )
    local, local_trace, local_closures = _trace_diagnostic(
        core.EnvelopeKind.LOCAL, generated, rank, carry, beta, envelope_cache
    )
    if complete.solver_status is core.V93SolverStatus.UNPROVEN_NUMERIC or local.solver_status is core.V93SolverStatus.UNPROVEN_NUMERIC:
        raise Pilot3Error("P0 numeric failure in exact access diagnostic")
    carry_hash = pilot1._hash_vector(carry.items())
    response_relation, _ = _response_relation(
        complete_record.candidate_response_time, local_record.candidate_response_time
    )
    union = sorted(set(complete_trace) | set(local_trace))
    access_rows = []
    local_only_keys = local_closures - complete_closures
    closure_rows = []
    target = generated.tasks[rank]
    hp_tasks = generated.tasks[:rank]
    lp_tasks = generated.tasks[rank + 1:]
    carry_key = tuple(sorted((str(name), int(value)) for name, value in carry.items()))
    def envelope_value(kind: core.EnvelopeKind, w: int, h: int, q: int) -> Fraction:
        cache_key = (kind.value, rank, carry_key, w, h, q)
        if cache_key not in envelope_cache:
            envelope_cache[cache_key] = core.exact_energy_envelope_v9_3(
                kind, target, hp_tasks, lp_tasks, w, q, h, 4, carry
            )
        return envelope_cache[cache_key]
    for w, h, q in union:
        complete_value = envelope_value(core.EnvelopeKind.COMPLETE, w, h, q)
        local_value = envelope_value(core.EnvelopeKind.LOCAL, w, h, q)
        envelope_relation = (
            "STRICT" if local_value < complete_value else
            "EQUAL" if local_value == complete_value else "VIOLATION"
        )
        if envelope_relation == "VIOLATION":
            raise Pilot3Error("P0 pointwise local envelope exceeded complete envelope")
        service = Fraction(generated.e0) + beta[h + q - 1]
        local_only = (w, h, q) in local_only_keys
        access_rows.append({
            "phase": phase, "cell_id": cell_id, "relation": relation,
            "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
            "exact_e0": _fraction_text(Fraction(generated.e0)),
            "task_id": target.name, "priority_rank": rank,
            "carry_in_vector_hash": carry_hash, "w": w, "h": h, "q": q,
            "q_plus_h_equals_w": q + h == w,
            "visited_by_complete": (w, h, q) in complete_trace,
            "visited_by_local": (w, h, q) in local_trace,
            "complete_envelope": _fraction_text(complete_value),
            "local_envelope": _fraction_text(local_value),
            "envelope_relation": envelope_relation,
            "service_value": _fraction_text(service),
            "complete_energy_satisfied": complete_value <= service,
            "local_energy_satisfied": local_value <= service,
            "complete_closure": (w, h, q) in complete_closures,
            "local_closure": (w, h, q) in local_closures,
            "local_only_closure": local_only,
            "predicted_interval_hit": local_value <= service < complete_value,
            "complete_candidate": complete_record.candidate_response_time,
            "local_candidate": local_record.candidate_response_time,
            "response_relation": response_relation,
        })
    for w, h, q in sorted(local_only_keys):
        closure_rows.append({
            "phase": phase, "cell_id": cell_id, "relation": relation,
            "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
            "exact_e0": _fraction_text(Fraction(generated.e0)),
            "task_id": target.name, "priority_rank": rank, "w": w, "h": h,
            "q": q,
            "complete_candidate": complete_record.candidate_response_time,
            "local_candidate": local_record.candidate_response_time,
            "earlier_than_complete_candidate": (
                complete_record.candidate_response_time is not None
                and w < complete_record.candidate_response_time
            ),
            "earlier_than_local_candidate": (
                local_record.candidate_response_time is not None
                and w < local_record.candidate_response_time
            ),
            "candidate_changed": response_relation == "TIGHTER",
        })
    truncated = (
        complete.solver_status is core.V93SolverStatus.UNPROVEN_TIMEOUT
        or local.solver_status is core.V93SolverStatus.UNPROVEN_TIMEOUT
    )
    diagnostic = {
        "access_diagnostic_status": "TRACED_TRUNCATED_60" if truncated else "TRACED_COMPLETE",
        "complete_diagnostic_solver_status": complete.solver_status.value,
        "local_diagnostic_solver_status": local.solver_status.value,
    }
    return access_rows, closure_rows, diagnostic


def _response_rows(
    phase: str, cell_id: str, generated: pilot1.GeneratedTaskset,
    results: Mapping[taskset.AnalysisVariant, taskset.TasksetAnalysisResult],
) -> List[Dict[str, Any]]:
    specs = (
        ("DEADLINE_CARRY_IN", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D),
        ("FIXED_CW_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW),
        ("RECURSIVE_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_LOC),
    )
    rows = []
    for relation, complete_variant, local_variant in specs:
        complete = results[complete_variant]
        local = results[local_variant]
        for rank, (left, right) in enumerate(zip(complete.task_records, local.task_records)):
            response_relation, improvement = _response_relation(
                left.candidate_response_time, right.candidate_response_time
            )
            certification_gain = (
                right.certification_status is taskset.TaskCertificationStatus.CERTIFIED
                and left.certification_status is not taskset.TaskCertificationStatus.CERTIFIED
            )
            if response_relation == "VIOLATION":
                raise Pilot3Error("P0 response dominance violation")
            rows.append({
                "phase": phase, "cell_id": cell_id, "relation": relation,
                "taskset_id": generated.taskset_id, "U_norm": generated.u_norm,
                "exact_e0": _fraction_text(Fraction(generated.e0)),
                "task_id": left.task_id, "priority_rank": rank,
                "complete_solver_status": left.solver_status.value,
                "local_solver_status": right.solver_status.value,
                "complete_certification_status": left.certification_status.value,
                "local_certification_status": right.certification_status.value,
                "complete_candidate": left.candidate_response_time,
                "local_candidate": right.candidate_response_time,
                "response_relation": response_relation, "improvement": improvement,
                "certification_gain": certification_gain,
                "access_diagnostic_status": (
                    "NOT_APPLICABLE_RECURSIVE" if relation == "RECURSIVE_CARRY_IN"
                    else "PENDING"
                ),
                "complete_diagnostic_solver_status": None,
                "local_diagnostic_solver_status": None,
            })
    return rows


def _access_diagnostics(
    phase: str, generated: pilot1.GeneratedTaskset,
    results: Mapping[taskset.AnalysisVariant, taskset.TasksetAnalysisResult],
    beta: Sequence[Fraction],
    envelope_cache: Optional[Dict[Tuple[Any, ...], Fraction]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    envelope_cache = envelope_cache if envelope_cache is not None else {}
    cell_id = f"U{generated.u_norm}-E0={_fraction_text(Fraction(generated.e0))}"
    response = _response_rows(phase, cell_id, generated, results)
    response_by_key = {
        (str(row["relation"]), str(row["task_id"])): row for row in response
    }
    access: List[Dict[str, Any]] = []
    closures: List[Dict[str, Any]] = []
    specs = (
        ("DEADLINE_CARRY_IN", taskset.AnalysisVariant.CW_D, taskset.AnalysisVariant.LOC_D),
        ("FIXED_CW_CARRY_IN", taskset.AnalysisVariant.CW_THETA_CW, taskset.AnalysisVariant.LOC_THETA_CW),
    )
    for relation, complete_variant, local_variant in specs:
        complete = results[complete_variant]
        local = results[local_variant]
        if relation == "DEADLINE_CARRY_IN":
            carry: Optional[Mapping[str, int]] = {item.name: item.deadline for item in generated.tasks}
        elif production_runner._source_is_jointly_certified(complete):
            carry = {
                record.task_id: int(record.candidate_response_time)
                for record in complete.task_records if record.candidate_response_time is not None
            }
            if len(carry) != len(generated.tasks):
                raise Pilot3Error("certified fixed-CW source has incomplete candidate vector")
        else:
            carry = None
        if carry is None:
            for record in complete.task_records:
                response_by_key[(relation, record.task_id)]["access_diagnostic_status"] = "SKIPPED_UNCERTIFIED_FIXED_CW_SOURCE"
            continue
        for rank, (left, right) in enumerate(zip(complete.task_records, local.task_records)):
            diagnostic_row = response_by_key[(relation, left.task_id)]
            if not (_record_evaluated(left) and _record_evaluated(right)):
                diagnostic_row["access_diagnostic_status"] = "SKIPPED_NOT_EVALUATED"
                continue
            if (
                left.solver_status is taskset.TaskSolverStatus.TIMEOUT
                or right.solver_status is taskset.TaskSolverStatus.TIMEOUT
            ):
                diagnostic_row["access_diagnostic_status"] = "SKIPPED_PRODUCTION_TIMEOUT"
                continue
            new_access, new_closures, diagnostic = _diagnose_pair_task(
                phase, cell_id, relation, generated, rank, carry, left, right, beta,
                envelope_cache,
            )
            diagnostic_row.update(diagnostic)
            access.extend(new_access)
            closures.extend(new_closures)
    return access, closures, response


def prepare_output(config_path: Path, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / "pilot3_config.yaml"
    if not target.exists():
        shutil.copyfile(config_path, target)


def run_selection(
    config: Mapping[str, Any], output_root: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    context = _pilot2_context(config)
    intervals = reconstruct_intervals(config, context)
    candidates, selected = rank_and_select_e0(intervals, config)
    _write_csv(output_root / "e0_intervals.csv", INTERVAL_COLUMNS, intervals)
    _write_csv(output_root / "e0_candidate_ranking.csv", CANDIDATE_COLUMNS, candidates)
    _write_csv(output_root / "selected_e0_values.csv", SELECTED_COLUMNS, selected)
    summary = {
        "interval_count": len(intervals),
        "candidate_count": len(candidates),
        "data_value_count": sum(row["selection_role"] == "DATA" for row in selected),
        "control_value_count": sum(row["selection_role"] == "CONTROL" for row in selected),
        "selected_values": [str(row["exact_e0"]) for row in selected],
        "selected_coverage": [{
            key: value for key, value in row.items() if not key.startswith("_")
        } for row in selected],
        "exact_arithmetic_only": True,
        "half_open_coverage_rule": "lower_bound_inclusive <= E0 < upper_bound_exclusive",
    }
    return intervals, selected, summary


def _save_failure_input(
    output_root: Path, stage: str, generated: Optional[pilot1.GeneratedTaskset],
) -> str:
    if generated is None:
        return ""
    path = output_root / "failure_inputs" / f"{stage}_{generated.taskset_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {
        "taskset_id": generated.taskset_id, "generation_seed": generated.seed,
        "U_norm": generated.u_norm, "E0": _fraction_text(Fraction(generated.e0)),
        "tasks": list(generated.task_payload),
    })
    return str(path.relative_to(output_root))


def run_screening(
    config: Mapping[str, Any], output_root: Path,
    selected: Sequence[Mapping[str, Any]], selection_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    context = _analysis_context(config, output_root)
    paired: List[pilot1.GeneratedTaskset] = []
    paired_rows = []
    for u_index, u_norm in enumerate(config["screening"]["normalized_utilizations"]):
        for taskset_index in range(config["screening"]["tasksets_per_utilization"]):
            generated = _generate_paired_taskset(
                config, context, u_norm, u_index, taskset_index,
                config["screening"]["base_seed"], "SCREENING",
            )
            paired.append(generated)
            paired_rows.append(_paired_taskset_row("SCREENING", generated))
    _write_csv(output_root / "paired_tasksets.csv", PAIRED_TASKSET_COLUMNS, paired_rows)

    output_files = (
        ("screening_results.csv", RESULT_COLUMNS),
        ("access_point_tightness.csv", ACCESS_COLUMNS),
        ("closure_differences.csv", CLOSURE_COLUMNS),
        ("response_differences.csv", RESPONSE_COLUMNS),
        ("timeout_reruns.csv", ATTEMPT_COLUMNS),
    )
    for filename, columns in output_files:
        _write_csv(output_root / filename, columns, [])
    attempt_rows: List[Dict[str, Any]] = []
    active: Optional[pilot1.GeneratedTaskset] = None
    active_variant = ""
    failures: List[Dict[str, Any]] = []
    diagnostic_caches: Dict[str, Dict[Tuple[Any, ...], Fraction]] = defaultdict(dict)
    try:
        for selected_row in selected:
            e0 = Fraction(str(selected_row["exact_e0"]))
            e0_index = int(selected_row["e0_index"])
            for generated_base in paired:
                active = replace(generated_base, e0=e0, e0_index=e0_index)
                results, result_rows = _run_five(
                    "SCREENING", generated_base, e0, e0_index, context,
                    attempt_rows, output_root / "timeout_reruns.csv",
                )
                _append_csv(output_root / "screening_results.csv", RESULT_COLUMNS, result_rows)
                access, closures, response = _access_diagnostics(
                    "SCREENING", active, results, context["beta"],
                    diagnostic_caches[generated_base.taskset_id],
                )
                _append_csv(output_root / "access_point_tightness.csv", ACCESS_COLUMNS, access)
                _append_csv(output_root / "closure_differences.csv", CLOSURE_COLUMNS, closures)
                _append_csv(output_root / "response_differences.csv", RESPONSE_COLUMNS, response)
    except BaseException as exc:
        failures.append({
            "severity": "P0", "stage": "screening",
            "taskset_id": active.taskset_id if active else None,
            "analysis_variant": active_variant, "code": type(exc).__name__,
            "detail": str(exc)[:1000],
            "input_file": _save_failure_input(output_root, "screening", active),
        })
        _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, failures)
        raise
    _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, failures)

    from scripts.analyze_v9_3_pilot3 import summarize_screening
    summary = summarize_screening(
        selected, paired_rows, _read_csv(output_root / "screening_results.csv"),
        _read_csv(output_root / "access_point_tightness.csv"),
        _read_csv(output_root / "closure_differences.csv"),
        _read_csv(output_root / "response_differences.csv"),
        _read_csv(output_root / "timeout_reruns.csv"), config,
    )
    summary["e0_selection"] = dict(selection_summary)
    _write_json(output_root / "screening_summary.json", summary)
    return summary


def write_interim_summary(output_root: Path) -> Dict[str, Any]:
    from scripts.analyze_v9_3_pilot3 import summarize_interim, write_interim_report

    summary = summarize_interim(
        _read_csv(output_root / "screening_results.csv"),
        _read_csv(output_root / "timeout_reruns.csv"),
        _read_csv(output_root / "access_point_tightness.csv"),
        _read_csv(output_root / "closure_differences.csv"),
        _read_csv(output_root / "response_differences.csv"),
    )
    selected = _read_csv(output_root / "selected_e0_values.csv")
    for u_norm in ("0.4", "0.6"):
        for row in selected:
            cell_id = f"U{u_norm}-E0={row['exact_e0']}"
            if cell_id not in summary["cells"]:
                summary["cells"][cell_id] = {
                    "U_norm": u_norm, "exact_e0": row["exact_e0"],
                    "sampled_tasksets": 0, "analyses": 0,
                    "sample_scope": "NOT_RUN_ADAPTIVE_GATE",
                    "class": "NOT_RUN_ADAPTIVE_GATE",
                }
    if (output_root / "adaptive_decisions.csv").exists():
        summary["adaptive_decisions"] = _read_csv(output_root / "adaptive_decisions.csv")
    summary["selected_exact_e0"] = [row["exact_e0"] for row in selected]
    _write_json(output_root / "interim_summary.json", summary)
    write_interim_report(output_root, summary)
    return summary


def finalize_adaptive(output_root: Path) -> Dict[str, Any]:
    """Freeze the adaptive checkpoint into the required Pilot-3 result artifacts."""

    summary = write_interim_summary(output_root)
    summary.update({
        "screening_design": "USER_DIRECTED_ADAPTIVE_E0_GATE",
        "formal_grid_completed": False,
        "parameter_identification_success": False,
        "confirmation": {
            "executed": False, "reason": "NO_D_OR_E_CELL",
            "selected_cells": [], "analysis_results": 0,
        },
        "severity": {
            "P0": [],
            "P1": ["no local-only closure, strict response, or certification gain observed"],
            "P2": [
                f"{summary['timeouts']['final_timeout_analyses']} final configuration timeouts",
                "five E0 values stopped after the two-taskset U=0.4 adaptive gate",
            ],
        },
        "next_single_structural_dimension": "constrained-deadline D/T distribution",
        "core_grid_readiness": False,
    })
    _write_json(output_root / "screening_summary.json", summary)
    if not (output_root / "confirmation_results.csv").exists():
        _write_csv(output_root / "confirmation_results.csv", RESULT_COLUMNS, [])
    if not (output_root / "failures.csv").exists():
        _write_csv(output_root / "failures.csv", FAILURE_COLUMNS, [])

    selected = _read_csv(output_root / "selected_e0_values.csv")
    coverage_lines = [
        (
            f"- `{row['exact_e0']}` ({row['selection_role']}): intervals="
            f"{row['covered_intervals']}, tasksets={row['covered_tasksets']}, "
            f"tasks={row['covered_tasks']}, variants={row['covered_variants']}, "
            f"U0.4/U0.6 intervals={row['covered_U_0_4_intervals']}/"
            f"{row['covered_U_0_6_intervals']}"
        )
        for row in selected
    ]
    cell_lines = [
        (
            f"- {cell_id}: {cell['class']}; scope={cell['sample_scope']}; "
            f"tasksets={cell['sampled_tasksets']}; strict-envelope="
            f"{cell.get('envelope_strict_accesses', 0)}; closure="
            f"{cell.get('local_only_closures', 0)}; strict-response="
            f"{cell.get('response_strict_tasks', 0)}; gain="
            f"{cell.get('certification_gain_tasksets', 0)}"
        )
        for cell_id, cell in sorted(summary["cells"].items())
    ]
    relation = summary["signals"]["relations"]
    lines = [
        "# ASAP-BLOCK v9.3 Pilot-3 adaptive exact-E0 report", "",
        "This is an adaptive diagnostic pilot. It is not a completed formal experiment or a paper-result claim.", "",
        "## Exact E0 selection and predicted coverage", "", *coverage_lines, "",
        "## Completed screening", "",
        f"- Complete taskset-E0 instances: {summary['completed_taskset_e0_instances']}",
        f"- Complete production analyses: {summary['completed_analyses']}",
        "- The original 70/350 full grid was paused after 20/100 and replaced by the user-directed adaptive gate.",
        "- Five remaining E0 values each stopped after two U=0.4 tasksets because no local-only closure appeared.",
        "", "## Cell outcomes", "", *cell_lines, "",
        "## Tightness and timeout", "",
        f"- Strict envelope accesses: {summary['signals']['strict_envelope_accesses']} / {summary['signals']['envelope_accesses']}",
        f"- Predicted energy-separation hits on new tasksets: {summary['signals']['predicted_energy_separation_hits']}",
        f"- Local-only closures: {summary['signals']['local_only_closures']}",
        f"- Strict response tasks: {summary['signals']['strict_response_tasks']}",
        f"- Certification gains: {summary['signals']['certification_gain_tasks']}",
        f"- 60s timeouts / 90s retries / timeout at 90s: {summary['timeouts']['initial_60_second_timeouts']} / {summary['timeouts']['conditional_90_second_attempts']} / {summary['timeouts']['timeout_at_90']}",
        "", "## Dominance", "",
        f"- Deadline relation: {relation['DEADLINE_CARRY_IN']['violations']} violations",
        f"- Fixed-CW relation: {relation['FIXED_CW_CARRY_IN']['violations']} violations",
        f"- Recursive relation: {relation['RECURSIVE_CARRY_IN']['violations']} violations",
        f"- Pointwise envelope violations: {summary['dominance']['envelope_violations']}",
        "", "## Decision", "",
        "No local-only closure, strict response, or certification gain was observed. Parameter identification therefore failed without implying that local refinement is ineffective.",
        "The strict envelope points did not cross the energy condition on the newly generated paired tasksets, so no earliest closure/candidate position changed.",
        "The next pilot may change exactly one structural dimension: constrained-deadline D/T distribution.",
        "There is not yet evidence to freeze a strict-response region or design the CORE-1/CORE-2 formal experiment grid.",
    ]
    (output_root / "pilot3_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _hash_files(output_root)
    return summary


def adaptive_gate_decision(
    gate: str, local_only_closures: int, strict_response_tasks: int,
    certification_gain_tasks: int,
) -> str:
    if gate == "INITIAL_U04_TWO":
        return "EXPAND_U04_TO_FIVE" if local_only_closures else "STOP_E0_NO_LOCAL_ONLY_CLOSURE"
    if gate == "FULL_U04_FIVE":
        return (
            "EXPAND_TO_U06" if strict_response_tasks or certification_gain_tasks
            else "STOP_E0_NO_STRICT_RESPONSE_OR_CERTIFICATION_GAIN"
        )
    raise Pilot3Error(f"unknown adaptive gate {gate}")


def _adaptive_counts(
    output_root: Path, e0: Fraction, taskset_ids: set,
) -> Tuple[int, int, int]:
    e0_text = _fraction_text(e0)
    closures = [
        row for row in _read_csv(output_root / "closure_differences.csv")
        if row["exact_e0"] == e0_text and row["taskset_id"] in taskset_ids
    ]
    responses = [
        row for row in _read_csv(output_root / "response_differences.csv")
        if row["exact_e0"] == e0_text and row["taskset_id"] in taskset_ids
    ]
    return (
        len(closures),
        sum(row["response_relation"] == "TIGHTER" for row in responses),
        sum(str(row["certification_gain"]).lower() == "true" for row in responses),
    )


def run_adaptive_screening(
    config: Mapping[str, Any], output_root: Path,
    selected: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Resume the paused run using the user-declared closure/response gates."""

    context = _analysis_context(config, output_root)
    paired_values = [_reconstruct_paired_taskset(row) for row in _read_csv(output_root / "paired_tasksets.csv")]
    paired = {(item.u_norm, item.taskset_index): item for item in paired_values}
    if len(paired) != 10:
        raise Pilot3Error("adaptive screening requires the ten frozen paired tasksets")
    attempt_rows: List[Dict[str, Any]] = _read_csv(output_root / "timeout_reruns.csv")
    decisions: List[Dict[str, Any]] = []
    decision_path = output_root / "adaptive_decisions.csv"
    _write_csv(decision_path, ADAPTIVE_DECISION_COLUMNS, [])
    diagnostic_caches: Dict[str, Dict[Tuple[Any, ...], Fraction]] = defaultdict(dict)

    def completed_keys() -> set:
        rows = _read_csv(output_root / "screening_results.csv")
        grouped: Dict[Tuple[str, str], set] = defaultdict(set)
        for row in rows:
            grouped[(row["taskset_id"], row["exact_e0"])].add(row["analysis_variant"])
        if any(len(values) != 5 for values in grouped.values()):
            raise Pilot3Error("adaptive input has a partial screening result instance")
        return set(grouped)

    def run_instance(e0: Fraction, e0_index: int, base: pilot1.GeneratedTaskset) -> None:
        key = (base.taskset_id, _fraction_text(e0))
        if key in completed_keys():
            return
        results, rows = _run_five_adaptive(
            base, e0, e0_index, context, attempt_rows,
            output_root / "timeout_reruns.csv",
        )
        _append_csv(output_root / "screening_results.csv", RESULT_COLUMNS, rows)
        generated = replace(base, e0=e0, e0_index=e0_index)
        access, closures, response = _access_diagnostics(
            "SCREENING", generated, results, context["beta"],
            diagnostic_caches[base.taskset_id],
        )
        _append_csv(output_root / "access_point_tightness.csv", ACCESS_COLUMNS, access)
        _append_csv(output_root / "closure_differences.csv", CLOSURE_COLUMNS, closures)
        _append_csv(output_root / "response_differences.csv", RESPONSE_COLUMNS, response)
        write_interim_summary(output_root)

    for selected_row in selected:
        e0 = Fraction(str(selected_row["exact_e0"]))
        e0_index = int(selected_row["e0_index"])
        e0_text = _fraction_text(e0)
        existing = {
            taskset_id for taskset_id, value in completed_keys() if value == e0_text
        }
        if len(existing) == 10:
            decisions.append({
                "exact_e0": e0_text, "gate": "PREEXISTING_FULL", "U_norm": "ALL",
                "tasksets_completed": 10, "analyses_completed": 50,
                "local_only_closures": None, "strict_response_tasks": None,
                "certification_gain_tasks": None, "decision": "KEEP_EXISTING_NO_RERUN",
            })
            _write_csv(decision_path, ADAPTIVE_DECISION_COLUMNS, decisions)
            continue
        if existing:
            raise Pilot3Error("adaptive E0 has an unsupported partially completed taskset set")

        initial_bases = [paired[("0.4", index)] for index in range(2)]
        for base in initial_bases:
            run_instance(e0, e0_index, base)
        initial_ids = {item.taskset_id for item in initial_bases}
        closure_count, strict_count, gain_count = _adaptive_counts(output_root, e0, initial_ids)
        decision = adaptive_gate_decision(
            "INITIAL_U04_TWO", closure_count, strict_count, gain_count
        )
        decisions.append({
            "exact_e0": e0_text, "gate": "INITIAL_U04_TWO", "U_norm": "0.4",
            "tasksets_completed": 2, "analyses_completed": 10,
            "local_only_closures": closure_count, "strict_response_tasks": strict_count,
            "certification_gain_tasks": gain_count, "decision": decision,
        })
        _write_csv(decision_path, ADAPTIVE_DECISION_COLUMNS, decisions)
        if decision != "EXPAND_U04_TO_FIVE":
            continue

        all_u04 = [paired[("0.4", index)] for index in range(5)]
        for base in all_u04[2:]:
            run_instance(e0, e0_index, base)
        all_u04_ids = {item.taskset_id for item in all_u04}
        closure_count, strict_count, gain_count = _adaptive_counts(output_root, e0, all_u04_ids)
        decision = adaptive_gate_decision(
            "FULL_U04_FIVE", closure_count, strict_count, gain_count
        )
        decisions.append({
            "exact_e0": e0_text, "gate": "FULL_U04_FIVE", "U_norm": "0.4",
            "tasksets_completed": 5, "analyses_completed": 25,
            "local_only_closures": closure_count, "strict_response_tasks": strict_count,
            "certification_gain_tasks": gain_count, "decision": decision,
        })
        _write_csv(decision_path, ADAPTIVE_DECISION_COLUMNS, decisions)
        if decision != "EXPAND_TO_U06":
            continue
        for index in range(5):
            run_instance(e0, e0_index, paired[("0.6", index)])
        decisions.append({
            "exact_e0": e0_text, "gate": "FULL_U06_FIVE", "U_norm": "0.6",
            "tasksets_completed": 10, "analyses_completed": 50,
            "local_only_closures": None, "strict_response_tasks": None,
            "certification_gain_tasks": None, "decision": "E0_ADAPTIVE_COMPLETE",
        })
        _write_csv(decision_path, ADAPTIVE_DECISION_COLUMNS, decisions)
    summary = write_interim_summary(output_root)
    summary["adaptive_decisions"] = decisions
    _write_json(output_root / "interim_summary.json", summary)
    return summary


def _cell_values(cell_id: str, screening_summary: Mapping[str, Any]) -> Tuple[str, Fraction]:
    cell = screening_summary["cells"][cell_id]
    return str(cell["U_norm"]), Fraction(str(cell["exact_e0"]))


def run_confirmation(
    config: Mapping[str, Any], output_root: Path,
    screening_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    selected_cells = list(screening_summary.get("selected_confirmation_cells", []))
    _write_csv(output_root / "confirmation_results.csv", RESULT_COLUMNS, [])
    if not selected_cells:
        from scripts.analyze_v9_3_pilot3 import summarize_confirmation
        return summarize_confirmation([], [], [])
    context = _analysis_context(config, output_root)
    attempt_rows = _read_csv(output_root / "timeout_reruns.csv")
    result_rows: List[Dict[str, Any]] = []
    response_rows: List[Dict[str, Any]] = []
    generated_cache: Dict[Tuple[int, int], pilot1.GeneratedTaskset] = {}
    utilization_values = [format(float(value), ".1f") for value in config["screening"]["normalized_utilizations"]]
    for cell_id in selected_cells:
        u_text, e0 = _cell_values(cell_id, screening_summary)
        try:
            u_index = utilization_values.index(u_text)
        except ValueError as exc:
            raise Pilot3Error(f"selected confirmation utilization {u_text} is not frozen") from exc
        for taskset_index in range(config["confirmation"]["tasksets_per_cell"]):
            cache_key = (u_index, taskset_index)
            if cache_key not in generated_cache:
                generated_cache[cache_key] = _generate_paired_taskset(
                    config, context, config["screening"]["normalized_utilizations"][u_index],
                    u_index, taskset_index, config["confirmation"]["base_seed"], "CONFIRMATION",
                )
            base = generated_cache[cache_key]
            e0_index = next(
                int(row["e0_index"]) for row in _read_csv(output_root / "selected_e0_values.csv")
                if Fraction(row["exact_e0"]) == e0
            )
            results, new_rows = _run_five(
                "CONFIRMATION", base, e0, e0_index, context, attempt_rows,
                output_root / "timeout_reruns.csv",
            )
            result_rows.extend(new_rows)
            _append_csv(output_root / "confirmation_results.csv", RESULT_COLUMNS, new_rows)
            generated = replace(base, e0=e0, e0_index=e0_index)
            response_rows.extend(_response_rows("CONFIRMATION", cell_id, generated, results))
    from scripts.analyze_v9_3_pilot3 import summarize_confirmation
    return summarize_confirmation(result_rows, response_rows, selected_cells)


def _earliest_boundary_summary(
    access_rows: Sequence[Mapping[str, Any]], closure_rows: Sequence[Mapping[str, Any]],
    response_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    predicted = [row for row in access_rows if str(row["predicted_interval_hit"]).lower() == "true"]
    closure = sorted(
        closure_rows,
        key=lambda row: (int(row["w"]), int(row["h"]), int(row["q"]), str(row["taskset_id"])),
    )
    candidate = sorted(
        (row for row in response_rows if row["response_relation"] == "TIGHTER"),
        key=lambda row: (int(row["local_candidate"]), str(row["taskset_id"])),
    )
    strict_access = sorted(
        (row for row in access_rows if row["envelope_relation"] == "STRICT"),
        key=lambda row: (int(row["w"]), int(row["h"]), int(row["q"]), str(row["taskset_id"])),
    )
    return {
        "predicted_coverage_hit_count": len(predicted),
        "first_local_only_closure": closure[0] if closure else None,
        "first_envelope_strict_access": strict_access[0] if strict_access else None,
        "first_response_strict": candidate[0] if candidate else None,
        "local_only_closures_before_complete_candidate": sum(
            str(row["earlier_than_complete_candidate"]).lower() == "true" for row in closure
        ),
        "local_only_closures_before_local_candidate": sum(
            str(row["earlier_than_local_candidate"]).lower() == "true" for row in closure
        ),
        "earliest_boundary_relation": (
            "RESPONSE" if candidate else "CLOSURE" if closure else
            "PREDICTED_ACCESS" if predicted else "NO_PREDICTED_ACCESS"
        ),
        "future_dimension": "constrained-deadline D/T structure",
    }


def _hash_files(output_root: Path) -> None:
    target = output_root / "file_hashes.sha256"
    lines = []
    for path in sorted(output_root.rglob("*"), key=lambda item: item.relative_to(output_root).as_posix()):
        if not path.is_file() or path == target:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(output_root).as_posix()}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def finalize(
    output_root: Path, selection_summary: Mapping[str, Any],
    screening_summary: Dict[str, Any], confirmation_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    access = _read_csv(output_root / "access_point_tightness.csv")
    closures = _read_csv(output_root / "closure_differences.csv")
    responses = _read_csv(output_root / "response_differences.csv")
    boundary = _earliest_boundary_summary(access, closures, responses)
    screening_summary["confirmation"] = dict(confirmation_summary)
    screening_summary["earliest_structural_boundary"] = boundary
    _write_json(output_root / "screening_summary.json", screening_summary)
    full = {
        "e0_selection": dict(selection_summary), "screening": screening_summary,
        "earliest_structural_boundary": boundary,
    }
    from scripts.analyze_v9_3_pilot3 import write_report
    write_report(output_root, full)
    _hash_files(output_root)
    return full


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "v9_3_pilot3.yaml")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--stage",
        choices=("selection", "screening", "interim", "adaptive", "confirmation", "all"),
        default="all",
    )
    return parser


def _selected_from_disk(output_root: Path) -> List[Dict[str, Any]]:
    return [{**row, "_value": Fraction(row["exact_e0"])} for row in _read_csv(output_root / "selected_e0_values.csv")]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        prepare_output(args.config, args.output_root)
        if args.stage in {"selection", "all"}:
            _intervals, selected, selection_summary = run_selection(config, args.output_root)
        else:
            selected = _selected_from_disk(args.output_root)
            selection_summary = {
                "interval_count": sum(1 for _ in _read_csv(args.output_root / "e0_intervals.csv")),
                "selected_values": [row["exact_e0"] for row in selected],
                "selected_coverage": [{key: value for key, value in row.items() if not key.startswith("_")} for row in selected],
            }
        if args.stage == "selection":
            _hash_files(args.output_root)
            print(json.dumps(selection_summary, ensure_ascii=False, sort_keys=True))
            return 0
        if args.stage == "interim":
            summary = write_interim_summary(args.output_root)
            _hash_files(args.output_root)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0
        if args.stage == "adaptive":
            run_adaptive_screening(config, args.output_root, selected)
            summary = finalize_adaptive(args.output_root)
            print(json.dumps({
                "completed_instances": summary["completed_taskset_e0_instances"],
                "completed_analyses": summary["completed_analyses"],
                "signals": summary["signals"],
                "adaptive_decisions": summary["adaptive_decisions"],
            }, ensure_ascii=False, sort_keys=True))
            return 0
        if args.stage in {"screening", "all"}:
            screening_summary = run_screening(config, args.output_root, selected, selection_summary)
        else:
            screening_summary = json.loads((args.output_root / "screening_summary.json").read_text(encoding="utf-8"))
        confirmation_summary = {"executed": False, "selected_cells": [], "analysis_results": 0, "cells": {}}
        if args.stage in {"confirmation", "all"}:
            if screening_summary.get("p0"):
                raise Pilot3Error("P0 gate prevents confirmation")
            confirmation_summary = run_confirmation(config, args.output_root, screening_summary)
        full = finalize(args.output_root, selection_summary, screening_summary, confirmation_summary)
        print(json.dumps({
            "stage": args.stage, "selected_e0": selection_summary["selected_values"],
            "paired_instances": screening_summary["paired_instances"],
            "d_or_e_cells": screening_summary["d_or_e_cells"],
            "confirmation_executed": confirmation_summary["executed"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"v9.3 Pilot-3 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
