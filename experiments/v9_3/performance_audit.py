"""Fail-closed plan and formal terminal audit for B4."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .performance_config import ALL_SCHEDULERS, OUTCOME_VERSION
from .performance_identity import audit_gate_formal_relationship
from .performance_identity import energy_identity, execution_identity, semantic_request_id


REQUEST_CONTRACT_VERSION = "ASAP_BLOCK_V9_3_B4_REQUEST_V1"


FORMAL_COUNTERS = (
    "missing_request", "duplicate_request", "partial_nine_scheduler_group",
    "runtime_timeout_after_retry", "simulator_internal_error", "trace_parse_error",
    "simulation_not_reached_horizon", "taskset_hash_mismatch", "priority_hash_mismatch",
    "power_hash_mismatch", "release_hash_mismatch", "nonzero_arrival_offset",
    "energy_identity_mismatch", "normalization_horizon_mismatch",
    "solar_scale_mismatch_across_gate_horizons", "outcome_contract_version_mismatch",
    "invalid_adjudicable_denominator", "metric_recomputation_mismatch",
    "canonical_request_identity_mismatch", "selected_gate_not_subset_of_formal",
    "unselected_horizon_present_in_formal", "source_or_binary_changed_after_CAL",
    "taskset_store_not_frozen", "silent_UNAVAILABLE_to_zero_conversion",
)


def _empty() -> Counter:
    return Counter({name: 0 for name in FORMAL_COUNTERS})


def load_terminal_results(root: Path) -> list:
    results = []
    for path in sorted(Path(root).glob("*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            results.append({"semantic_request_id": path.stem, "trace_parse_error": True})
    return results


def audit_formal_results(
    plan: Mapping[str, Any], results: Iterable[Mapping[str, Any]], *,
    selected_gate_ids: Optional[Iterable[str]] = None,
    unselected_gate_ids: Optional[Iterable[str]] = None,
    taskset_store_frozen: bool = True,
    calibration_source_commit: Optional[str] = None,
    calibration_binary_sha256: Optional[str] = None,
    gate_energy_identity_shared: Optional[bool] = None,
) -> Dict[str, Any]:
    counters = _empty()
    planned = {str(row["semantic_request_id"]): row for row in plan.get("requests", [])}
    if len(plan.get("requests", [])) != 43200:
        counters["missing_request"] += abs(43200 - len(plan.get("requests", [])))
    if len(planned) != len(plan.get("requests", [])):
        counters["duplicate_request"] += len(plan.get("requests", [])) - len(planned)
    observed = defaultdict(list)
    for result in results:
        observed[str(result.get("semantic_request_id", ""))].append(result)
    counters["duplicate_request"] += sum(max(0, len(values) - 1) for values in observed.values())
    counters["missing_request"] += len(set(planned) - set(observed))
    for request_id, values in observed.items():
        result = values[0]
        expected = planned.get(request_id)
        if expected is None:
            counters["canonical_request_identity_mismatch"] += 1
            continue
        if result.get("trace_parse_error"):
            counters["trace_parse_error"] += 1
        attempts = result.get("attempts", [])
        if attempts and attempts[-1].get("legacy_status") == "SIM_RUNTIME_TIMEOUT":
            counters["runtime_timeout_after_retry"] += 1
        if result.get("legacy_status") == "SIM_INTERNAL_ERROR":
            counters["simulator_internal_error"] += 1
        if result.get("completion_reason") != "reached_horizon":
            counters["simulation_not_reached_horizon"] += 1
        if result.get("arrival_offsets_zero") is not True:
            counters["nonzero_arrival_offset"] += 1
        for field, counter in (
            ("taskset_semantic_hash", "taskset_hash_mismatch"),
            ("priority_hash", "priority_hash_mismatch"),
            ("power_hash", "power_hash_mismatch"),
            ("release_hash", "release_hash_mismatch"),
            ("energy_identity", "energy_identity_mismatch"),
        ):
            if result.get(field) != expected.get(field):
                counters[counter] += 1
        energy = result.get("energy_material", {})
        try:
            if energy_identity(energy) != result.get("energy_identity"):
                counters["energy_identity_mismatch"] += 1
        except (TypeError, ValueError):
            counters["energy_identity_mismatch"] += 1
        if energy.get("normalization_horizon_ms") != 60000:
            counters["normalization_horizon_mismatch"] += 1
        outcome = result.get("outcome", {})
        if outcome.get("contract_version") != OUTCOME_VERSION:
            counters["outcome_contract_version_mismatch"] += 1
        if outcome.get("adjudicable_jobs", 0) <= 0 or len(outcome.get("tasks", [])) != 10:
            counters["invalid_adjudicable_denominator"] += 1
        denominator = int(outcome.get("adjudicable_jobs", 0))
        tasks = outcome.get("tasks", [])
        if denominator > 0:
            expected_metrics = {
                "jmr": int(outcome.get("missed_jobs", 0)) / denominator,
                "completion_ratio": int(outcome.get("completed_inside_window", 0)) / denominator,
            }
            for metric, ranks in (("jmr_top_m", 4), ("jmr_top_25_percent", 3)):
                selected_tasks = [task for task in tasks if int(task.get("priority_rank", -1)) < ranks]
                selected_denominator = sum(int(task.get("adjudicable_jobs", 0)) for task in selected_tasks)
                expected_metrics[metric] = (
                    "UNAVAILABLE" if selected_denominator == 0 else
                    sum(int(task.get("missed_jobs", 0)) for task in selected_tasks) / selected_denominator
                )
            for metric, expected_metric in expected_metrics.items():
                observed_metric = outcome.get(metric)
                if expected_metric == "UNAVAILABLE":
                    mismatch = observed_metric != "UNAVAILABLE"
                else:
                    try:
                        mismatch = abs(float(observed_metric) - expected_metric) > 1e-12
                    except (TypeError, ValueError):
                        mismatch = True
                if mismatch:
                    counters["metric_recomputation_mismatch"] += 1
            expected_pass = (
                result.get("terminal") is True
                and result.get("simulation_completed") is True
                and result.get("completion_reason") == "reached_horizon"
                and int(outcome.get("missed_jobs", 0)) == 0
                and bool(tasks) and all(bool(task.get("minimum_jobs_satisfied")) for task in tasks)
            )
            if bool(outcome.get("observed_pass")) != expected_pass:
                counters["metric_recomputation_mismatch"] += 1
        try:
            recomputed_request_id = semantic_request_id(
                contract_version=REQUEST_CONTRACT_VERSION,
                taskset_semantic_hash=str(result["taskset_semantic_hash"]),
                energy_identity_value=str(result["energy_identity"]),
                scheduler_id=str(result["scheduler_id"]),
                runtime_horizon_ms=int(result["runtime_horizon_ms"]),
                simulation_semantic_config_hash=str(result["simulation_semantic_config_hash"]),
            )
            if recomputed_request_id != request_id:
                counters["canonical_request_identity_mismatch"] += 1
            if execution_identity(
                request_id, str(plan["source_commit"]), str(plan["simulator_binary_sha256"]),
            ) != result.get("execution_identity"):
                counters["canonical_request_identity_mismatch"] += 1
        except (KeyError, TypeError, ValueError):
            counters["canonical_request_identity_mismatch"] += 1
        top_denominators = {
            "jmr": denominator, "completion_ratio": denominator,
            "jmr_top_m": sum(int(task.get("adjudicable_jobs", 0)) for task in tasks if int(task.get("priority_rank", -1)) < 4),
            "jmr_top_25_percent": sum(int(task.get("adjudicable_jobs", 0)) for task in tasks if int(task.get("priority_rank", -1)) < 3),
        }
        for metric, metric_denominator in top_denominators.items():
            if metric_denominator == 0 and outcome.get(metric) != "UNAVAILABLE":
                counters["silent_UNAVAILABLE_to_zero_conversion"] += 1
    groups = defaultdict(set)
    for request in plan.get("requests", []):
        key = (
            request["taskset_semantic_hash"], request["energy_condition"],
            request["runtime_horizon_ms"],
        )
        groups[key].add(request["scheduler_id"])
    counters["partial_nine_scheduler_group"] = sum(
        1 for schedulers in groups.values() if set(schedulers) != set(ALL_SCHEDULERS)
    )
    if not taskset_store_frozen:
        counters["taskset_store_not_frozen"] += 1
    if calibration_source_commit is not None and calibration_source_commit != plan.get("source_commit"):
        counters["source_or_binary_changed_after_CAL"] += 1
    if calibration_binary_sha256 is not None and calibration_binary_sha256 != plan.get("simulator_binary_sha256"):
        counters["source_or_binary_changed_after_CAL"] += 1
    if gate_energy_identity_shared is not True:
        counters["solar_scale_mismatch_across_gate_horizons"] += 1
    if selected_gate_ids is not None and unselected_gate_ids is not None:
        try:
            audit_gate_formal_relationship(selected_gate_ids, unselected_gate_ids, planned)
        except ValueError as exc:
            text = str(exc)
            if "strict_subset" in text:
                counters["selected_gate_not_subset_of_formal"] += 1
            else:
                counters["unselected_horizon_present_in_formal"] += 1
    else:
        counters["selected_gate_not_subset_of_formal"] += 1
        counters["unselected_horizon_present_in_formal"] += 1
    complete = all(counters[name] == 0 for name in FORMAL_COUNTERS)
    return {
        "schema": "ASAP_BLOCK_V9_3_B4_FORMAL_AUDIT_V1",
        "status": "FORMAL_COMPLETE" if complete else "FORMAL_INCOMPLETE",
        "counters": dict(counters), "planned_requests": len(planned),
        "observed_requests": len(observed),
    }


def audit_plan_counts(config_stage: str, plan: Mapping[str, Any]) -> Dict[str, Any]:
    expected = {"CALIBRATION": 6750, "HORIZON_GATE": 4000, "FORMAL": 43200}
    observed = len(plan.get("requests", []))
    target = expected.get(config_stage, observed)
    ids = [str(row.get("semantic_request_id")) for row in plan.get("requests", [])]
    return {
        "expected_requests": target, "observed_requests": observed,
        "duplicate_request_ids": len(ids) - len(set(ids)),
        "valid": observed == target and len(ids) == len(set(ids)),
    }
