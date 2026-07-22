"""Deterministic 30/60-second horizon gate for B4."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .performance_config import (
    CONFIRMATORY_COMPARISONS, FORMAL_UTILIZATIONS, OUTCOME_VERSION,
    PRIMARY_SCHEDULERS,
)
from .performance_engine import REQUEST_CONTRACT_VERSION
from .performance_identity import energy_identity, execution_identity, semantic_request_id


SELECT_30S = "SELECT_30S"
SELECT_60S = "SELECT_60S"
INVALID_GATE = "INVALID_GATE"


@dataclass(frozen=True)
class HorizonDecision:
    state: str
    selected_horizon_ms: int
    pass_ratio_stable: bool
    paired_direction_stable: bool
    adjudicable_contract_satisfied: bool
    technical_identity_complete: bool
    diagnostics: Mapping[str, Any]

    def document(self) -> Dict[str, Any]:
        return asdict(self)


def select_gate_tasksets(tasksets: Iterable[Mapping[str, Any]], per_utilization: int = 50) -> Tuple[Mapping[str, Any], ...]:
    groups = defaultdict(list)
    for taskset in tasksets:
        groups[str(taskset["utilization"])].append(taskset)
    selected = []
    for utilization in sorted(groups):
        ordered = sorted(groups[utilization], key=lambda row: str(row["taskset_semantic_hash"]))
        if len(ordered) < per_utilization:
            raise ValueError(f"insufficient frozen tasksets for gate at {utilization}")
        selected.extend(ordered[:per_utilization])
    return tuple(selected)


def _outcome_recomputed(outcome: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    try:
        tasks = outcome["tasks"]
        denominator = sum(int(task["adjudicable_jobs"]) for task in tasks)
        misses = sum(int(task["missed_jobs"]) for task in tasks)
        completed = sum(int(task["completed_inside_window"]) for task in tasks)
        minimum = all(bool(task["minimum_jobs_satisfied"]) for task in tasks)
        expected_pass = (
            result.get("terminal") is True
            and result.get("simulation_completed") is True
            and result.get("completion_reason") == "reached_horizon"
            and misses == 0 and minimum
        )
        return (
            outcome.get("contract_version") == OUTCOME_VERSION
            and int(outcome["adjudicable_jobs"]) == denominator
            and int(outcome["missed_jobs"]) == misses
            and int(outcome["completed_inside_window"]) == completed
            and bool(outcome["observed_pass"]) == expected_pass
        )
    except (KeyError, TypeError, ValueError):
        return False


def gate_rows_from_terminal(
    plan: Mapping[str, Any], results: Iterable[Mapping[str, Any]],
) -> Tuple[Tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    """Convert real terminal JSON to the fail-closed gate decision table."""

    requests = list(plan.get("requests", []))
    planned = {str(row.get("semantic_request_id")): row for row in requests}
    result_list = list(results)
    observed: Dict[str, list] = defaultdict(list)
    for result in result_list:
        observed[str(result.get("semantic_request_id", ""))].append(result)
    complete_ids = (
        len(requests) == 4000 and len(planned) == 4000
        and set(observed) == set(planned)
        and all(len(values) == 1 for values in observed.values())
    )
    rows = []
    pair_energy = defaultdict(dict)
    for request_id, expected in planned.items():
        values = observed.get(request_id, [])
        result = values[0] if len(values) == 1 else {}
        identity_valid = complete_ids
        for field in (
            "taskset_semantic_hash", "priority_hash", "power_hash", "release_hash",
            "energy_identity", "scheduler_id", "runtime_horizon_ms",
            "simulation_semantic_config_hash", "execution_identity",
        ):
            identity_valid = identity_valid and result.get(field) == expected.get(field)
        identity_valid = identity_valid and result.get("arrival_offsets_zero") is True
        try:
            identity_valid = identity_valid and energy_identity(result["energy_material"]) == result["energy_identity"]
            identity_valid = identity_valid and semantic_request_id(
                contract_version=REQUEST_CONTRACT_VERSION,
                taskset_semantic_hash=str(result["taskset_semantic_hash"]),
                energy_identity_value=str(result["energy_identity"]),
                scheduler_id=str(result["scheduler_id"]),
                runtime_horizon_ms=int(result["runtime_horizon_ms"]),
                simulation_semantic_config_hash=str(result["simulation_semantic_config_hash"]),
            ) == request_id
            identity_valid = identity_valid and execution_identity(
                request_id, str(plan["source_commit"]), str(plan["simulator_binary_sha256"]),
            ) == result["execution_identity"]
        except (KeyError, TypeError, ValueError):
            identity_valid = False
        outcome = result.get("outcome", {})
        minimum = bool(outcome.get("tasks")) and all(
            bool(task.get("minimum_jobs_satisfied")) for task in outcome.get("tasks", [])
        )
        horizon = int(expected["runtime_horizon_ms"])
        pair_key = (str(expected["taskset_semantic_hash"]), str(expected["scheduler_id"]))
        pair_energy[pair_key][horizon] = str(expected["energy_identity"])
        rows.append({
            "semantic_request_id": request_id,
            "taskset_semantic_hash": expected["taskset_semantic_hash"],
            "u_norm": expected["u_norm"], "scheduler_id": expected["scheduler_id"],
            "horizon_ms": horizon,
            "observed_pass": bool(outcome.get("observed_pass", False)),
            "identity_valid": identity_valid,
            "outcome_recomputed": _outcome_recomputed(outcome, result),
            "minimum_jobs_satisfied": minimum,
            "simulation_reached_horizon": (
                result.get("terminal") is True
                and result.get("simulation_completed") is True
                and result.get("completion_reason") == "reached_horizon"
            ),
        })
    energy_pairs_valid = all(
        set(values) == {30000, 60000} and values[30000] == values[60000]
        for values in pair_energy.values()
    ) and len(pair_energy) == 2000
    if not energy_pairs_valid:
        rows = [{**row, "identity_valid": False} for row in rows]
    return tuple(rows), {
        "complete_request_id_set": complete_ids,
        "energy_identity_shared_across_horizons": energy_pairs_valid,
        "planned_requests": len(requests), "observed_requests": len(result_list),
    }


def decide_horizon_gate(rows: Iterable[Mapping[str, Any]], *, expected_requests: int = 4000) -> HorizonDecision:
    rows = list(rows)
    technical_fields = (
        "identity_valid", "outcome_recomputed", "simulation_reached_horizon",
    )
    technical = len(rows) == expected_requests and all(
        all(bool(row.get(field)) for field in technical_fields) for row in rows
    )
    by_cell = defaultdict(list)
    for row in rows:
        by_cell[(str(row["u_norm"]), str(row["scheduler_id"]), int(row["horizon_ms"]))].append(bool(row["observed_pass"]))
    expected_cells = {
        (utilization, scheduler, horizon)
        for utilization in FORMAL_UTILIZATIONS
        for scheduler in PRIMARY_SCHEDULERS
        for horizon in (30000, 60000)
    }
    if set(by_cell) != expected_cells or any(len(values) != 50 for values in by_cell.values()):
        technical = False
    if len({str(row.get("semantic_request_id")) for row in rows}) != len(rows):
        technical = False
    changes = {}
    pass_stable = True
    dimensions = {(key[0], key[1]) for key in by_cell}
    for utilization, scheduler in dimensions:
        if scheduler not in PRIMARY_SCHEDULERS:
            continue
        short = by_cell.get((utilization, scheduler, 30000), [])
        long = by_cell.get((utilization, scheduler, 60000), [])
        if not short or len(short) != len(long):
            technical = False
            continue
        change = abs(sum(short) / len(short) - sum(long) / len(long))
        changes[f"{utilization}:{scheduler}"] = change
        pass_stable = pass_stable and change <= 0.05 + 1e-15

    indexed = defaultdict(dict)
    for row in rows:
        key = (str(row["taskset_semantic_hash"]), int(row["horizon_ms"]))
        scheduler = str(row["scheduler_id"])
        if scheduler in indexed[key]:
            technical = False
        indexed[key][scheduler] = bool(row["observed_pass"])
    if len(indexed) != 800 or any(set(group) != set(PRIMARY_SCHEDULERS) for group in indexed.values()):
        technical = False
    paired = {}
    paired_stable = True
    for left, right in CONFIRMATORY_COMPARISONS:
        effects = {}
        for horizon in (30000, 60000):
            values = []
            for (taskset_hash, observed_horizon), group in indexed.items():
                if observed_horizon != horizon:
                    continue
                if left not in group or right not in group:
                    technical = False
                    continue
                values.append(int(group[left]) - int(group[right]))
            if not values:
                technical = False
                effects[horizon] = 0.0
            else:
                effects[horizon] = sum(values) / len(values)
        same_direction = effects[30000] * effects[60000] >= 0
        both_small = abs(effects[30000]) < 0.02 and abs(effects[60000]) < 0.02
        stable = same_direction or both_small
        paired_stable = paired_stable and stable
        paired[f"{left}-{right}"] = {"effects": effects, "stable": stable}
    adjudicable = all(bool(row.get("minimum_jobs_satisfied")) for row in rows)
    if not technical or not adjudicable:
        state, horizon = INVALID_GATE, 0
    elif pass_stable and paired_stable:
        state, horizon = SELECT_30S, 30000
    else:
        state, horizon = SELECT_60S, 60000
    return HorizonDecision(
        state, horizon, pass_stable, paired_stable, adjudicable, technical,
        {"pass_ratio_changes": changes, "paired_effects": paired, "request_count": len(rows)},
    )
