"""Authoritative, fail-closed plan/result/store audit for B4 CAL phases."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .config import domain_hash
from .performance_config import CAL_UTILIZATIONS, OUTCOME_VERSION, PRIMARY_SCHEDULERS
from .performance_config import INITIAL_ETAS, INITIAL_KAPPAS
from .performance_identity import (
    REQUEST_CONTRACT_VERSION, energy_identity, execution_identity,
    semantic_request_id,
)


CAL_AUDIT_SCHEMA = "ASAP_BLOCK_V9_3_B4_CAL_AUDIT_V1"
CAL_AUDIT_DOMAIN = "ASAP_BLOCK:V9.3:B4:CAL_AUDIT:v1"
CAL_RESULT_DOMAIN = "ASAP_BLOCK:V9.3:B4:CAL_TERMINAL_RESULT:v1"
CAL_RESULT_SET_DOMAIN = "ASAP_BLOCK:V9.3:B4:CAL_TERMINAL_RESULT_SET:v1"

CAL_COUNTERS = (
    "wrong_plan_count", "duplicate_planned_request", "missing_result",
    "duplicate_result", "extra_result", "wrong_horizon", "wrong_scheduler",
    "not_terminal", "simulation_not_completed", "simulation_not_reached_horizon",
    "timeout_after_retry", "simulator_internal_error", "trace_parse_error",
    "outcome_contract_mismatch", "execution_identity_mismatch",
    "taskset_identity_mismatch", "priority_identity_mismatch",
    "power_identity_mismatch", "release_identity_mismatch",
    "energy_identity_mismatch", "canonical_request_identity_mismatch",
    "nonzero_arrival_offset", "taskset_not_in_manifest",
    "wrong_taskset_count", "incomplete_scheduler_pairing",
    "wrong_energy_condition_set", "manifest_not_calibration_store",
)


@dataclass(frozen=True)
class CalibrationPhaseAudit:
    phase: str
    status: str
    counters: Mapping[str, int]
    planned_requests: int
    observed_results: int
    audited_rows: Tuple[Mapping[str, Any], ...]
    terminal_result_set_identity: str
    audit_identity: str
    paired_counts: Tuple[Mapping[str, Any], ...]

    def document(self) -> Dict[str, Any]:
        return {
            "schema": CAL_AUDIT_SCHEMA, "phase": self.phase,
            "status": self.status, "counters": dict(self.counters),
            "planned_requests": self.planned_requests,
            "observed_results": self.observed_results,
            "terminal_result_set_identity": self.terminal_result_set_identity,
            "audit_identity": self.audit_identity,
            "paired_counts": list(self.paired_counts),
        }


def _expected_horizon(phase: str) -> int:
    if phase in {"initial", "extension_a", "extension_b"}:
        return 10000
    if phase in {"confirmation", "confirmation_full_grid"}:
        return 30000
    raise ValueError(f"unknown CAL phase: {phase}")


def _expected_count(phase: str, requests: Sequence[Mapping[str, Any]]) -> int:
    if phase == "initial":
        return 6750
    if phase == "confirmation":
        return 1350
    cells = {
        (str(request["energy_material"]["kappa"]), str(request["energy_material"]["eta"]))
        for request in requests
    }
    return len(cells) * len(CAL_UTILIZATIONS) * len(PRIMARY_SCHEDULERS) * 30


def _result_set_identity(results: Sequence[Mapping[str, Any]]) -> str:
    rows = sorted(
        (
            str(result.get("semantic_request_id", "")),
            domain_hash(CAL_RESULT_DOMAIN, result),
        )
        for result in results
    )
    return domain_hash(CAL_RESULT_SET_DOMAIN, rows)


def audit_calibration_phase(
    phase: str, plan: Mapping[str, Any], results: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> CalibrationPhaseAudit:
    """Close one CAL phase before exposing any row to the Q-only selector."""

    requests = list(plan.get("requests", []))
    result_list = list(results)
    counters = Counter({name: 0 for name in CAL_COUNTERS})
    expected_horizon = _expected_horizon(phase)
    expected_count = _expected_count(phase, requests)
    if len(requests) != expected_count:
        counters["wrong_plan_count"] += abs(expected_count - len(requests)) or 1

    planned: Dict[str, Mapping[str, Any]] = {}
    for request in requests:
        request_id = str(request.get("semantic_request_id", ""))
        if not request_id or request_id in planned:
            counters["duplicate_planned_request"] += 1
        planned[request_id] = request

    observed: Dict[str, list] = defaultdict(list)
    for result in result_list:
        observed[str(result.get("semantic_request_id", ""))].append(result)
    counters["missing_result"] += len(set(planned) - set(observed))
    counters["extra_result"] += len(set(observed) - set(planned))
    counters["duplicate_result"] += sum(max(0, len(values) - 1) for values in observed.values())

    manifest_entries = list(manifest.get("entries", []))
    manifest_hashes = {str(entry.get("taskset_semantic_hash", "")) for entry in manifest_entries}
    manifest_by_utilization = defaultdict(set)
    for entry in manifest_entries:
        manifest_by_utilization[str(entry.get("utilization", ""))].add(
            str(entry.get("taskset_semantic_hash", ""))
        )
    if (
        manifest.get("seed_space") != "ASAP_BLOCK_V9_3_B4_CAL_R1"
        or len(manifest_entries) != 90
        or manifest.get("configured_tasksets_per_utilization") != 30
    ):
        counters["manifest_not_calibration_store"] += 1

    rows = []
    group_tasksets: Dict[tuple, set] = defaultdict(set)
    cell_scheduler_sets: Dict[tuple, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    conditions = set()
    for request_id, expected in planned.items():
        values = observed.get(request_id, [])
        if len(values) != 1:
            continue
        result = values[0]
        horizon = int(expected.get("runtime_horizon_ms", -1))
        scheduler = str(expected.get("scheduler_id", ""))
        if horizon != expected_horizon or int(result.get("runtime_horizon_ms", -1)) != expected_horizon:
            counters["wrong_horizon"] += 1
        if scheduler not in PRIMARY_SCHEDULERS or result.get("scheduler_id") != scheduler:
            counters["wrong_scheduler"] += 1
        if result.get("terminal") is not True:
            counters["not_terminal"] += 1
        if result.get("simulation_completed") is not True:
            counters["simulation_not_completed"] += 1
        if result.get("completion_reason") != "reached_horizon":
            counters["simulation_not_reached_horizon"] += 1
        attempts = result.get("attempts", [])
        if result.get("legacy_status") == "SIM_RUNTIME_TIMEOUT" or (
            attempts and attempts[-1].get("legacy_status") == "SIM_RUNTIME_TIMEOUT"
        ):
            counters["timeout_after_retry"] += 1
        if result.get("legacy_status") == "SIM_INTERNAL_ERROR":
            counters["simulator_internal_error"] += 1
        if result.get("trace_parse_error") or str(result.get("legacy_reason", "")).startswith("trace_semantic_error:"):
            counters["trace_parse_error"] += 1
        outcome = result.get("outcome", {})
        if outcome.get("contract_version") != OUTCOME_VERSION:
            counters["outcome_contract_mismatch"] += 1
        if result.get("arrival_offsets_zero") is not True:
            counters["nonzero_arrival_offset"] += 1

        for field, counter in (
            ("taskset_semantic_hash", "taskset_identity_mismatch"),
            ("priority_hash", "priority_identity_mismatch"),
            ("power_hash", "power_identity_mismatch"),
            ("release_hash", "release_identity_mismatch"),
            ("energy_identity", "energy_identity_mismatch"),
        ):
            if result.get(field) != expected.get(field):
                counters[counter] += 1
        try:
            if energy_identity(result["energy_material"]) != result.get("energy_identity"):
                counters["energy_identity_mismatch"] += 1
            canonical = semantic_request_id(
                contract_version=REQUEST_CONTRACT_VERSION,
                taskset_semantic_hash=str(result["taskset_semantic_hash"]),
                energy_identity_value=str(result["energy_identity"]),
                scheduler_id=str(result["scheduler_id"]),
                runtime_horizon_ms=int(result["runtime_horizon_ms"]),
                simulation_semantic_config_hash=str(result["simulation_semantic_config_hash"]),
            )
            if canonical != request_id:
                counters["canonical_request_identity_mismatch"] += 1
            expected_execution = execution_identity(
                request_id, str(plan["source_commit"]), str(plan["simulator_binary_sha256"]),
            )
            if result.get("execution_identity") != expected.get("execution_identity") or result.get("execution_identity") != expected_execution:
                counters["execution_identity_mismatch"] += 1
        except (KeyError, TypeError, ValueError):
            counters["canonical_request_identity_mismatch"] += 1

        taskset_hash = str(expected.get("taskset_semantic_hash", ""))
        utilization = str(expected.get("u_norm", ""))
        if (
            taskset_hash not in manifest_hashes
            or taskset_hash not in manifest_by_utilization.get(utilization, set())
        ):
            counters["taskset_not_in_manifest"] += 1
        material = expected.get("energy_material", {})
        kappa, eta = str(material.get("kappa", "")), str(material.get("eta", ""))
        condition = str(expected.get("energy_condition", ""))
        conditions.add(condition)
        group = (kappa, eta, utilization, scheduler)
        group_tasksets[group].add(taskset_hash)
        cell_scheduler_sets[(kappa, eta, utilization)][scheduler].add(taskset_hash)
        rows.append({
            "kappa": kappa, "eta": eta, "u_norm": utilization,
            "scheduler_id": scheduler, "taskset_id": taskset_hash,
            "observed_pass": bool(outcome.get("observed_pass", False)),
        })

    paired_counts = []
    for group, tasksets in sorted(group_tasksets.items()):
        paired_counts.append({
            "kappa": group[0], "eta": group[1], "u_norm": group[2],
            "scheduler_id": group[3], "distinct_tasksets": len(tasksets),
        })
        if len(tasksets) != 30:
            counters["wrong_taskset_count"] += 1
        if tasksets != manifest_by_utilization.get(group[2], set()):
            counters["incomplete_scheduler_pairing"] += 1
    for cell, scheduler_sets in cell_scheduler_sets.items():
        if set(scheduler_sets) != set(PRIMARY_SCHEDULERS):
            counters["incomplete_scheduler_pairing"] += 1
            continue
        frozen = scheduler_sets[PRIMARY_SCHEDULERS[0]]
        if any(scheduler_sets[scheduler] != frozen for scheduler in PRIMARY_SCHEDULERS[1:]):
            counters["incomplete_scheduler_pairing"] += 1

    cells = {
        (str(request.get("energy_material", {}).get("kappa", "")),
         str(request.get("energy_material", {}).get("eta", "")))
        for request in requests
    }
    if phase == "initial" and cells != {
        (kappa, eta) for kappa in INITIAL_KAPPAS for eta in INITIAL_ETAS
    }:
        counters["wrong_energy_condition_set"] += 1
    if phase == "extension_a" and (
        len({kappa for kappa, _eta in cells}) != 1
        or not cells
        or any(eta not in {"1/4", "2"} for _kappa, eta in cells)
    ):
        counters["wrong_energy_condition_set"] += 1
    if phase == "extension_b" and cells != {
        (kappa, eta) for kappa in INITIAL_KAPPAS for eta in ("1/4", "2")
    }:
        counters["wrong_energy_condition_set"] += 1
    if phase == "confirmation" and conditions != {"low", "transition", "high"}:
        counters["wrong_energy_condition_set"] += 1
    result_set_identity = _result_set_identity(result_list)
    material = {
        "schema": CAL_AUDIT_SCHEMA, "phase": phase,
        "plan_identity": plan.get("formal_plan_identity"),
        "taskset_store_identity": manifest.get("store_identity"),
        "terminal_result_set_identity": result_set_identity,
        "counters": dict(counters), "paired_counts": paired_counts,
    }
    audit_identity = domain_hash(CAL_AUDIT_DOMAIN, material)
    status = "CAL_VALID" if all(counters[name] == 0 for name in CAL_COUNTERS) else "CAL_INVALID"
    return CalibrationPhaseAudit(
        phase, status, dict(counters), len(requests), len(result_list),
        tuple(rows), result_set_identity, audit_identity, tuple(paired_counts),
    )
