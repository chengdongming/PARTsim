"""Minimal deterministic planning and execution support for PERF-G.

This module owns only the PERF-G scientific contract.  Task generation and
the simulator remain the existing v9.3 implementations; this file supplies
their inputs, paired request identities, and the Q-only calibration rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .config import canonical_json, fraction_text, validate_config
from .simulation_engine import construct_paired_harvest_trace
from .taskset_store import TasksetStore, StoredTaskset, prepare_service_curve


PERF_G_DOMAIN = "ASAP_BLOCK:PERF_G:v4.1"
PROCESSORS = 4
TASK_COUNT = 10
FORMAL_HORIZON_MS = 60000
CAL_CONFIRMATION_HORIZON_MS = 30000
CAL_INITIAL_HORIZON_MS = 10000
FORMAL_MIN_ADJUDICABLE_JOBS = 100
FORMAL_TIMEOUT_SECONDS = 300
FORMAL_RETRY_TIMEOUT_SECONDS = 600
PERIOD_MIN_MS = 40
PERIOD_MAX_MS = 200
UTILIZATION_TOLERANCE = Fraction("1/100")
MIN_TASK_UTILIZATION = Fraction("1/100")
MAX_TASK_UTILIZATION = Fraction("4/5")
WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")

FORMAL_UTILIZATIONS = tuple(Fraction(value, 10) for value in range(1, 9))
FORMAL_TASKSETS_PER_UTILIZATION = 100
CAL_UTILIZATIONS = (Fraction("3/10"), Fraction("1/2"), Fraction("7/10"))
CAL_TASKSETS_PER_UTILIZATION = 30
CAL_SATURATION_CONDITION = {"name": "SAT", "kappa": "200", "eta": "2"}
CAL_KAPPAS = (Fraction(10), Fraction(50), Fraction(200))
CAL_ETAS = tuple(Fraction(value) for value in ("1/2", "3/4", "1", "5/4", "3/2"))
CAL_EXTENSION_ETAS = (Fraction("1/4"), Fraction(2))
CAL_SCHEDULERS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK",
)
FORMAL_SCHEDULERS = (
    "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC",
    "ALAP-BLOCK", "ALAP-NONBLOCK", "ALAP-SYNC",
    "ST-BLOCK", "ST-NONBLOCK", "ST-SYNC",
)
SCHEDULER_CLI = {
    "ASAP-BLOCK": "gpfp_asap_block",
    "ASAP-NONBLOCK": "gpfp_asap_nonblock",
    "ASAP-SYNC": "gpfp_asap_sync",
    "ALAP-BLOCK": "gpfp_alap_block",
    "ALAP-NONBLOCK": "gpfp_alap_nonblock",
    "ALAP-SYNC": "gpfp_alap_sync",
    "ST-BLOCK": "gpfp_st_block",
    "ST-NONBLOCK": "gpfp_st_nonblock",
    "ST-SYNC": "gpfp_st_sync",
}
SMOKE_CONDITIONS = (
    {"name": "LOW", "kappa": "10", "eta": "1/2", "smoke_only": True},
    {"name": "TRANSITION", "kappa": "50", "eta": "1", "smoke_only": True},
    {"name": "HIGH", "kappa": "200", "eta": "3/2", "smoke_only": True},
)
CAL_SEED = 410731
FORMAL_SEED = 910427
SMOKE_SEED = 710213
# Ordinary/general random Scheduler LOAD-CROSS service material.  This is
# deliberately independent of the B4 priority-energy experiment template.
BASE_SYSTEM_TEMPLATE = "system_config_unified_template.yml"


class PerfGError(ValueError):
    """Raised when a PERF-G plan or result violates its frozen contract."""


def _canonical_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def _as_fraction(value: Any, label: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise PerfGError(f"{label} is not an exact rational") from exc
    return result


@dataclass(frozen=True)
class PairedRetentionPolicy:
    """Explicit CAL retention policy; selection results remain data-driven."""

    reference_utilization: Fraction = Fraction("1/2")
    low_max_retention: Fraction = Fraction("1/5")
    transition_min_retention: Fraction = Fraction("1/5")
    transition_max_retention: Fraction = Fraction("4/5")
    high_min_retention: Fraction = Fraction("4/5")

    def __post_init__(self) -> None:
        fields = (
            "reference_utilization", "low_max_retention",
            "transition_min_retention", "transition_max_retention",
            "high_min_retention",
        )
        for field in fields:
            value = _as_fraction(getattr(self, field), field)
            if not Fraction(0) <= value <= Fraction(1):
                raise PerfGError(f"{field} must be in [0, 1]")
            object.__setattr__(self, field, value)
        if self.transition_min_retention > self.transition_max_retention:
            raise PerfGError("transition retention bounds are inverted")


def _coerce_paired_policy(policy: PairedRetentionPolicy | Mapping[str, Any] | None) -> PairedRetentionPolicy:
    if policy is None:
        return PairedRetentionPolicy()
    if isinstance(policy, PairedRetentionPolicy):
        return policy
    if isinstance(policy, Mapping):
        return PairedRetentionPolicy(**dict(policy))
    raise PerfGError("threshold_policy must be a PairedRetentionPolicy or mapping")


def _paired_policy_payload(policy: PairedRetentionPolicy) -> dict[str, str]:
    return {
        "reference_utilization": fraction_text(policy.reference_utilization),
        "low_max_retention": fraction_text(policy.low_max_retention),
        "transition_min_retention": fraction_text(policy.transition_min_retention),
        "transition_max_retention": fraction_text(policy.transition_max_retention),
        "high_min_retention": fraction_text(policy.high_min_retention),
    }


def condition(name: str, kappa: Any, eta: Any, *, smoke_only: bool = False) -> dict[str, Any]:
    kappa_f = _as_fraction(kappa, "kappa")
    eta_f = _as_fraction(eta, "eta")
    if kappa_f <= 0 or eta_f <= 0:
        raise PerfGError("kappa and eta must be positive")
    return {
        "name": str(name), "kappa": fraction_text(kappa_f),
        "eta": fraction_text(eta_f), "smoke_only": bool(smoke_only),
    }


def taskset_key(namespace: str, utilization: Fraction, index: int) -> str:
    return _canonical_hash(PERF_G_DOMAIN + ":TASKSET", {
        "namespace": namespace, "U_norm": fraction_text(utilization), "index": index,
    })[:24]


def request_id(taskset_id_value: str, energy_condition: str, scheduler: str) -> str:
    return "perf-g-" + _canonical_hash(PERF_G_DOMAIN + ":REQUEST", {
        "taskset_id": taskset_id_value,
        "energy_condition": energy_condition,
        "scheduler": scheduler,
    })


def _taskset_rows(namespace: str, utilizations: Sequence[Fraction], count: int) -> list[dict[str, Any]]:
    return [
        {
            "taskset_id": taskset_key(namespace, utilization, index),
            "U_norm": fraction_text(utilization),
            "taskset_index": index,
            "seed_namespace": namespace,
        }
        for utilization in utilizations
        for index in range(count)
    ]


def _request_rows(
    tasksets: Sequence[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]],
    schedulers: Sequence[str],
    *,
    horizon_ms: int,
    kind: str,
) -> list[dict[str, Any]]:
    rows = []
    for taskset in tasksets:
        for energy in conditions:
            for scheduler in schedulers:
                if scheduler not in SCHEDULER_CLI:
                    raise PerfGError(f"unknown scheduler: {scheduler}")
                rows.append({
                    "request_id": request_id(
                        str(taskset["taskset_id"]), str(energy["name"]), scheduler,
                    ),
                    "kind": kind,
                    "taskset_id": taskset["taskset_id"],
                    "U_norm": taskset["U_norm"],
                    "taskset_index": taskset["taskset_index"],
                    "energy_condition": energy["name"],
                    "kappa": energy.get("kappa"),
                    "eta": energy.get("eta"),
                    "scheduler": scheduler,
                    "scheduler_cli": SCHEDULER_CLI[scheduler],
                    "horizon_ms": horizon_ms,
                })
    return rows


def validate_pairing(rows: Sequence[Mapping[str, Any]], schedulers: Sequence[str]) -> dict[str, int]:
    expected = set(schedulers)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    seen_ids: set[str] = set()
    duplicate = 0
    for row in rows:
        request = str(row.get("request_id"))
        if request in seen_ids:
            duplicate += 1
        seen_ids.add(request)
        key = (str(row.get("taskset_id")), str(row.get("energy_condition")))
        groups.setdefault(key, []).append(row)
    partial = sum(
        set(str(row.get("scheduler")) for row in members) != expected
        or len(members) != len(expected)
        for members in groups.values()
    )
    missing = sum(max(0, len(expected) - len(members)) for members in groups.values())
    return {
        "groups": len(groups), "missing": missing, "duplicate": duplicate,
        "partial_group": partial,
    }


def cal_plan() -> dict[str, Any]:
    tasksets = _taskset_rows("CAL", CAL_UTILIZATIONS, CAL_TASKSETS_PER_UTILIZATION)
    conditions = [
        condition(f"k{kappa_text}-e{eta_text}", kappa_text, eta_text)
        for kappa_text in ("10", "50", "200")
        for eta_text in ("1/2", "3/4", "1", "5/4", "3/2")
    ]
    requests = _request_rows(tasksets, conditions, CAL_SCHEDULERS,
                             horizon_ms=CAL_INITIAL_HORIZON_MS, kind="CAL")
    return _plan_summary(tasksets, conditions, CAL_SCHEDULERS, requests, "CAL")


def cal_confirmation_conditions(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        condition(
            CAL_SATURATION_CONDITION["name"],
            CAL_SATURATION_CONDITION["kappa"],
            CAL_SATURATION_CONDITION["eta"],
        ),
        *[
            condition(name, selection[name]["kappa"], selection[name]["eta"])
            for name in ("LOW", "TRANSITION", "HIGH")
        ],
    ]


def cal_confirmation_plan(selection: Mapping[str, Any]) -> dict[str, Any]:
    tasksets = _taskset_rows("CAL", CAL_UTILIZATIONS, CAL_TASKSETS_PER_UTILIZATION)
    conditions = cal_confirmation_conditions(selection)
    requests = _request_rows(
        tasksets, conditions, CAL_SCHEDULERS,
        horizon_ms=CAL_CONFIRMATION_HORIZON_MS, kind="CAL_CONFIRM",
    )
    return _plan_summary(tasksets, conditions, CAL_SCHEDULERS, requests, "CAL_CONFIRM")


def formal_plan(selection: Mapping[str, Any] | None = None) -> dict[str, Any]:
    tasksets = _taskset_rows("FORMAL", FORMAL_UTILIZATIONS, FORMAL_TASKSETS_PER_UTILIZATION)
    if selection is None:
        conditions = [condition(name, 1, 1) for name in ("LOW", "TRANSITION", "HIGH")]
        executable = False
    else:
        conditions = [
            condition(name, selection[name]["kappa"], selection[name]["eta"])
            for name in ("LOW", "TRANSITION", "HIGH")
        ]
        executable = True
    requests = _request_rows(tasksets, conditions, FORMAL_SCHEDULERS,
                             horizon_ms=FORMAL_HORIZON_MS, kind="FORMAL")
    result = _plan_summary(tasksets, conditions, FORMAL_SCHEDULERS, requests, "FORMAL")
    result["executable_formal"] = executable
    return result


def _plan_summary(tasksets, conditions, schedulers, requests, kind):
    pairing = validate_pairing(requests, schedulers)
    return {
        "kind": kind,
        "unique_tasksets": len(tasksets),
        "energy_cells": len(conditions),
        "schedulers": len(schedulers),
        "requests": len(requests),
        "tasksets": tasksets,
        "energy_conditions": list(conditions),
        "schedulers_list": list(schedulers),
        "pairing": pairing,
        "requests_rows": requests,
    }


def _task_generation_config(namespace: str, utilizations: Sequence[Fraction], count: int) -> dict[str, Any]:
    raw = {
        "experiment_id": f"perf-g-{namespace.lower()}", "core": "CORE-3",
        "platform": {"cores": [PROCESSORS], "task_count": [TASK_COUNT]},
        "generation": {
            "deadline_mode": "constrained", "power_mode": "generator_default_heterogeneous",
            "priority_policy": "RM", "wcet_rounding": "compensated",
            "period_min": PERIOD_MIN_MS, "period_max": PERIOD_MAX_MS,
            "min_task_util": fraction_text(MIN_TASK_UTILIZATION),
            "max_task_util": fraction_text(MAX_TASK_UTILIZATION),
            "utilization_tolerance": fraction_text(UTILIZATION_TOLERANCE),
            "workload_candidates": list(WORKLOADS),
            "arrival_offset": False,
            "constrained_deadline": {
                "distribution": "generator_uniform_integer", "d_over_t_values": [],
                "d_over_t_min": "0", "d_over_t_max": "1",
            },
            "generator_timeout_seconds": 120,
        },
        "energy": {
            "initial_energy_values": ["0"], "exact_rational_encoding": "canonical_fraction",
            "battery_mode": "finite", "battery_capacity": "1",
            "simulation_initial_battery": "1",
            "service_curve": {
                "id": f"perf-g-raw-{namespace.lower()}", "horizon": FORMAL_HORIZON_MS,
                "system_template": BASE_SYSTEM_TEMPLATE, "solar_scale": "1",
                "require_real_solar_data": True,
            },
        },
        "grid": {
            "utilization_points": [fraction_text(value) for value in utilizations],
            "tasksets_per_cell": count,
            "base_seed": CAL_SEED if namespace == "CAL" else FORMAL_SEED if namespace == "FORMAL" else SMOKE_SEED,
            "seed_mode": "generation_dimensions",
        },
        "rta": {
            "methods": ["CW_THETA_CW", "LOC_THETA_LOC"], "timeout_seconds": 1,
            "retry_timeout_seconds": 2, "retry_policy": "timeout_once",
        },
        "analysis": {
            "variants": ["CW_THETA_CW", "LOC_THETA_LOC"], "timeout_seconds": 1,
            "retry_timeout_seconds": 2, "retry_policy": "timeout_once",
            "worker_count": 1, "numerical_mode": "EXACT_RATIONAL",
        },
        "simulation": {
            "horizon": 1000, "warmup": 0, "minimum_jobs_per_task": 1,
            "maximum_horizon": 1000, "horizon_extension_policy": "none",
            "trace_mode": "semantic", "deadline_miss_fail_fast": False,
            "trace_on_failure": True, "timeout_seconds": 30,
            "simulator_bin": "./build/rtsim/rtsim", "reuse_across_e0": False,
        },
        "execution": {
            "checkpoint_every": 1, "worker_count": 1,
            "output_root": ".", "taskset_store": ".",
            "resume": False, "fail_fast_on_p0": True, "preserve_attempt_history": True,
        },
    }
    return validate_config(raw, expected_core="CORE-3")


def materialize_tasksets(root: Path, namespace: str, utilizations: Sequence[Fraction], count: int) -> tuple[list[StoredTaskset], Any]:
    config = _task_generation_config(namespace, utilizations, count)
    service = prepare_service_curve(config, Path(root) / "service")
    store = TasksetStore(Path(root) / "tasksets", config, service)
    cells = __import__("experiments.v9_3.cell_model", fromlist=["expand_cells"]).expand_cells(config)
    if len(cells) != len(utilizations):
        raise PerfGError("unexpected PERF-G taskset generation cells")
    tasksets = []
    for cell in cells:
        for index in range(count):
            taskset = store.get_or_create(cell, index)
            if len(taskset.tasks) != TASK_COUNT or taskset.processors != PROCESSORS:
                raise PerfGError("generated taskset dimensions mismatch")
            if any(int(item.get("arrival_offset", 0)) != 0 for item in taskset.task_payload):
                raise PerfGError("PERF-G requires synchronous arrival_offset=0")
            if any(item.get("workload") == "idle" for item in taskset.task_payload):
                raise PerfGError("idle is not a PERF-G real-time workload")
            tasksets.append(taskset)
    store.verify_pairing_manifest(require_complete=True)
    return tasksets, service


def energy_material(taskset: StoredTaskset, energy: Mapping[str, Any], raw_trace: Sequence[Fraction]) -> dict[str, Any]:
    powers = [Fraction(item["P"]) for item in taskset.task_payload]
    demand = sum(
        Fraction(item["C"], item["T"]) * Fraction(item["P"])
        for item in taskset.task_payload
    )
    burst = sum(sorted(powers, reverse=True)[: min(PROCESSORS, TASK_COUNT)])
    battery = _as_fraction(energy["kappa"], "kappa") * burst
    raw_reference = sum(raw_trace, Fraction(0)) / FORMAL_HORIZON_MS
    if raw_reference <= 0:
        raise PerfGError("raw solar reference mean must be positive")
    eta = _as_fraction(energy["eta"], "eta")
    scale = eta * demand / raw_reference
    return {
        "kappa": fraction_text(_as_fraction(energy["kappa"], "kappa")),
        "eta": fraction_text(eta), "P_dem_j_per_tick": fraction_text(demand),
        "E_burst_j": fraction_text(burst), "battery_capacity_j": fraction_text(battery),
        "initial_energy_j": fraction_text(battery / 2),
        "raw_reference_mean_j_per_tick": fraction_text(raw_reference),
        "solar_scale": fraction_text(scale), "normalization_horizon_ms": FORMAL_HORIZON_MS,
    }


def build_raw_trace(service: Any) -> tuple[Fraction, ...]:
    trace = tuple(construct_paired_harvest_trace(service.system_path, FORMAL_HORIZON_MS))
    if len(trace) != FORMAL_HORIZON_MS:
        raise PerfGError("raw solar trace does not cover 60000 ticks")
    return trace


def select_transition(q: Mapping[tuple[str, str, str], float], *, utilizations=CAL_UTILIZATIONS) -> dict[str, Any] | None:
    candidates = []
    for kappa in sorted({key[0] for key in q}):
        eta_values_for_kappa = {key[1] for key in q if key[0] == kappa}
        for eta in sorted(eta_values_for_kappa, key=Fraction):
            required_keys = [
                (kappa, eta, fraction_text(utilization))
                for utilization in utilizations
            ]
            if any(key not in q for key in required_keys):
                continue
            values = [float(q[key]) for key in required_keys]
            n_t = sum(0.2 <= value <= 0.8 for value in values)
            if n_t >= 2:
                candidates.append((
                    -n_t, sum(abs(value - 0.5) for value in values),
                    abs(float(Fraction(eta) - 1)), Fraction(kappa), Fraction(eta),
                ))
    if not candidates:
        return None
    selected = min(candidates)
    return {"kappa": fraction_text(selected[3]), "eta": fraction_text(selected[4]), "N_T": -selected[0]}


def select_three_conditions(rows: Sequence[Mapping[str, Any]], *, utilizations=CAL_UTILIZATIONS) -> dict[str, Any] | None:
    q = q_matrix(rows)
    transition = select_transition(q, utilizations=utilizations)
    if transition is None:
        return None
    kappa, eta_t = transition["kappa"], Fraction(transition["eta"])
    eta_values = sorted({Fraction(key[1]) for key in q if key[0] == kappa})
    lower = [
        eta for eta in eta_values
        if eta < eta_t
        and (kappa, fraction_text(eta), "1/2") in q
        and q[(kappa, fraction_text(eta), "1/2")] <= 0.2
    ]
    higher = [
        eta for eta in eta_values
        if eta > eta_t
        and (kappa, fraction_text(eta), "1/2") in q
        and q[(kappa, fraction_text(eta), "1/2")] >= 0.8
    ]
    if not lower or not higher:
        return None
    eta_low, eta_high = max(lower), min(higher)
    return {
        "kappa_star": kappa, "eta_low": fraction_text(eta_low),
        "eta_transition": fraction_text(eta_t), "eta_high": fraction_text(eta_high),
        "LOW": {"kappa": kappa, "eta": fraction_text(eta_low)},
        "TRANSITION": {"kappa": kappa, "eta": fraction_text(eta_t)},
        "HIGH": {"kappa": kappa, "eta": fraction_text(eta_high)},
    }


def q_matrix(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], float]:
    grouped: dict[tuple[str, str, str, str], list[float]] = {}
    for row in rows:
        key = (
            str(row["kappa"]), str(row["eta"]), str(row["U_norm"]),
            str(row["scheduler"]),
        )
        grouped.setdefault(key, []).append(1.0 if row.get("taskset_pass") is True else 0.0)
    by_cell: dict[tuple[str, str, str], list[float]] = {}
    for (kappa, eta, utilization, _scheduler), values in grouped.items():
        by_cell.setdefault((kappa, eta, utilization), []).append(sum(values) / len(values))
    return {key: float(median(values)) for key, values in by_cell.items()}


def q_only_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("kappa", "eta", "U_norm", "taskset_id", "taskset_pass")}


def _paired_condition_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        fraction_text(_as_fraction(row.get("kappa"), "kappa")),
        fraction_text(_as_fraction(row.get("eta"), "eta")),
        fraction_text(_as_fraction(row.get("U_norm"), "U_norm")),
        str(row.get("scheduler")),
    )


def _paired_row_metric(row: Mapping[str, Any], name: str) -> Any:
    value = row.get(name)
    if value is not None:
        return value
    metrics = row.get("metrics")
    return metrics.get(name) if isinstance(metrics, Mapping) else None


def _paired_cells(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], dict[tuple[str, str, str], list[Mapping[str, Any]]]]:
    cells: dict[
        tuple[str, str, str, str],
        dict[tuple[str, str, str], list[Mapping[str, Any]]],
    ] = {}
    for row in rows:
        condition_key = _paired_condition_key(row)
        pair_key = (
            condition_key[2], condition_key[3], str(row.get("taskset_id")),
        )
        cells.setdefault(condition_key, {}).setdefault(pair_key, []).append(row)
    return cells


def paired_retention_matrix(
    rows: Sequence[Mapping[str, Any]],
    saturation_condition: Mapping[str, Any],
    *,
    utilizations: Sequence[Fraction] = CAL_UTILIZATIONS,
    schedulers: Sequence[str] = CAL_SCHEDULERS,
) -> dict[str, Any]:
    """Compute paired retention against an explicit saturated reference.

    The denominator is made only from tasksets that pass at SAT for the same
    utilization and scheduler.  Technical failures, missing pairs, duplicate
    identities, and taskset-hash mismatches are reported as incomplete rather
    than converted to failures.  Tuple-keyed ``cells`` and ``aggregates`` are
    intentionally kept as an analysis-layer structure, not serialized output.
    """
    sat_kappa = fraction_text(_as_fraction(saturation_condition.get("kappa"), "kappa"))
    sat_eta = fraction_text(_as_fraction(saturation_condition.get("eta"), "eta"))
    sat_condition = (sat_kappa, sat_eta)
    cell_rows = _paired_cells(rows)
    condition_keys = sorted({key[:2] for key in cell_rows}, key=lambda key: (Fraction(key[0]), Fraction(key[1])))
    if sat_condition not in condition_keys:
        condition_keys.append(sat_condition)
    scheduler_values = tuple(schedulers) or tuple(sorted({key[3] for key in cell_rows}))
    cells: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for kappa, eta in condition_keys:
        for utilization in utilizations:
            utilization_text = fraction_text(utilization)
            for scheduler in scheduler_values:
                sat_key = (sat_kappa, sat_eta, utilization_text, str(scheduler))
                candidate_key = (kappa, eta, utilization_text, str(scheduler))
                sat_members = cell_rows.get(sat_key, {})
                candidate_members = cell_rows.get(candidate_key, {})
                sat_duplicates = sum(len(members) != 1 for members in sat_members.values())
                candidate_duplicates = sum(len(members) != 1 for members in candidate_members.values())
                sat_rows = [row for members in sat_members.values() for row in members]
                candidate_rows = [row for members in candidate_members.values() for row in members]
                sat_technical = sum(row.get("taskset_pass") is None for row in sat_rows)
                candidate_technical = sum(row.get("taskset_pass") is None for row in candidate_rows)
                hash_mismatch = 0
                for pair_key in set(sat_members) & set(candidate_members):
                    sat_hash = sat_members[pair_key][0].get("taskset_hash")
                    candidate_hash = candidate_members[pair_key][0].get("taskset_hash")
                    if sat_hash is not None and candidate_hash is not None and sat_hash != candidate_hash:
                        hash_mismatch += 1

                sat_pass_keys = {
                    pair_key for pair_key, members in sat_members.items()
                    if len(members) == 1 and members[0].get("taskset_pass") is True
                }
                missing_candidate = sum(pair_key not in candidate_members for pair_key in sat_pass_keys)
                candidate_pass_keys = {
                    pair_key for pair_key in sat_pass_keys
                    if len(candidate_members.get(pair_key, ())) == 1
                    and candidate_members[pair_key][0].get("taskset_pass") is True
                }
                denominator = len(sat_pass_keys)
                retained = len(candidate_pass_keys)
                incomplete = bool(
                    sat_duplicates or candidate_duplicates or sat_technical
                    or candidate_technical or hash_mismatch or missing_candidate
                )
                if denominator == 0:
                    status = "UNAVAILABLE"
                    retention = None
                elif incomplete:
                    status = "INCOMPLETE"
                    retention = None
                else:
                    status = "AVAILABLE"
                    retention = retained / denominator
                cells[(kappa, eta, utilization_text, str(scheduler))] = {
                    "kappa": kappa, "eta": eta, "U_norm": utilization_text,
                    "scheduler": str(scheduler), "status": status,
                    "retention": retention, "sat_denominator_count": denominator,
                    "retained_count": retained, "sat_observed_count": len(sat_rows),
                    "candidate_observed_count": len(candidate_rows),
                    "sat_technical_failure_count": sat_technical,
                    "candidate_technical_failure_count": candidate_technical,
                    "missing_candidate_pair_count": missing_candidate,
                    "duplicate_identity_count": sat_duplicates + candidate_duplicates,
                    "taskset_hash_mismatch_count": hash_mismatch,
                    "pairing_complete": not incomplete and denominator > 0,
                }

    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}
    for kappa, eta in condition_keys:
        for utilization in utilizations:
            utilization_text = fraction_text(utilization)
            members = [
                cells[(kappa, eta, utilization_text, str(scheduler))]
                for scheduler in scheduler_values
            ]
            valid = [member for member in members if member["status"] == "AVAILABLE"]
            retentions = [member["retention"] for member in valid]
            if not valid:
                status = (
                    "INCOMPLETE"
                    if any(member["status"] == "INCOMPLETE" for member in members)
                    else "UNAVAILABLE"
                )
                retention = None
            elif len(valid) != len(members):
                status = "PARTIAL"
                retention = float(median(retentions))
            else:
                status = "AVAILABLE"
                retention = float(median(retentions))
            aggregates[(kappa, eta, utilization_text)] = {
                "kappa": kappa, "eta": eta, "U_norm": utilization_text,
                "status": status, "retention": retention,
                "valid_scheduler_count": len(valid),
                "scheduler_count": len(members),
                "sat_denominator_count": sum(member["sat_denominator_count"] for member in valid),
                "retained_count": sum(member["retained_count"] for member in valid),
                "unavailable_scheduler_count": sum(member["status"] == "UNAVAILABLE" for member in members),
                "incomplete_scheduler_count": sum(member["status"] == "INCOMPLETE" for member in members),
            }
    return {
        "saturation_condition": {"kappa": sat_kappa, "eta": sat_eta},
        "cells": cells, "aggregates": aggregates,
    }


def paired_saturation_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    saturation_condition: Mapping[str, Any],
    *,
    reference_utilization: Fraction = Fraction("1/2"),
    schedulers: Sequence[str] = CAL_SCHEDULERS,
) -> list[dict[str, Any]]:
    """Return auditable energy-blocking diagnostics for each condition."""
    reference = fraction_text(reference_utilization)
    conditions = sorted(
        {(fraction_text(_as_fraction(row.get("kappa"), "kappa")),
          fraction_text(_as_fraction(row.get("eta"), "eta"))) for row in rows},
        key=lambda key: (Fraction(key[0]), Fraction(key[1])),
    )
    diagnostics = []
    for kappa, eta in conditions:
        values = []
        missing_count = 0
        for row in rows:
            row_key = _paired_condition_key(row)
            if row_key[:3] == (kappa, eta, reference) and row_key[3] in schedulers:
                metric = _paired_row_metric(row, "energy_blocked_ticks")
                if isinstance(metric, (int, float)):
                    values.append(float(metric))
                else:
                    missing_count += 1
        diagnostics.append({
            "kappa": kappa, "eta": eta, "U_norm": reference,
            "observed_count": len(values),
            "missing_count": missing_count,
            "energy_blocking_complete": missing_count == 0 and bool(values),
            "energy_blocked_positive_count": sum(value > 0 for value in values),
            "energy_blocked_ticks_sum": sum(values),
            "energy_blocked_ticks_max": max(values) if values else None,
            "energy_blocked_zero_ratio": (
                sum(value == 0 for value in values) / len(values) if values else None
            ),
            "energy_blocked_zero": bool(values) and all(value == 0 for value in values),
        })
    return diagnostics


def _paired_aggregate_usable(aggregate: Mapping[str, Any]) -> bool:
    """Return whether an aggregate is safe for paired selection."""
    return (
        aggregate.get("status") in {"AVAILABLE", "PARTIAL"}
        and aggregate.get("valid_scheduler_count", 0) > 0
        and aggregate.get("incomplete_scheduler_count", 0) == 0
    )


def select_calibration_paired(
    rows: Sequence[Mapping[str, Any]],
    saturation_condition: Mapping[str, Any],
    *,
    threshold_policy: PairedRetentionPolicy | Mapping[str, Any] | None = None,
    utilizations: Sequence[Fraction] = CAL_UTILIZATIONS,
    schedulers: Sequence[str] = CAL_SCHEDULERS,
) -> dict[str, Any]:
    """Select CAL conditions using an explicit, data-driven policy."""
    policy = _coerce_paired_policy(threshold_policy)
    matrix = paired_retention_matrix(
        rows, saturation_condition, utilizations=utilizations, schedulers=schedulers,
    )
    diagnostics = paired_saturation_diagnostics(
        rows, saturation_condition, reference_utilization=policy.reference_utilization,
        schedulers=schedulers,
    )
    reference = fraction_text(policy.reference_utilization)
    diag_by_condition = {(row["kappa"], row["eta"]): row for row in diagnostics}
    candidates = []
    for key, aggregate in sorted(matrix["aggregates"].items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]))):
        if key[2] != reference:
            continue
        candidate = dict(aggregate)
        candidate["is_saturation"] = (key[0], key[1]) == (
            matrix["saturation_condition"]["kappa"],
            matrix["saturation_condition"]["eta"],
        )
        candidate["energy_diagnostics"] = diag_by_condition.get((key[0], key[1]))
        aggregates_by_u = {
            utilization: matrix["aggregates"][(key[0], key[1], utilization)]
            for utilization in map(fraction_text, utilizations)
        }
        retention_by_u = {
            utilization: aggregate["retention"]
            for utilization, aggregate in aggregates_by_u.items()
        }
        available_values = [value for value in retention_by_u.values() if value is not None]
        transition_values = [
            value for value in available_values
            if policy.transition_min_retention <= Fraction(str(value)) <= policy.transition_max_retention
        ]
        candidate["retention_by_u"] = retention_by_u
        candidate["usable"] = _paired_aggregate_usable(candidate)
        candidate["all_required_u_usable"] = all(
            _paired_aggregate_usable(aggregate)
            for aggregate in aggregates_by_u.values()
        )
        candidate["transition_N_T"] = len(transition_values)
        candidate["transition_deviation"] = sum(
            abs(float(value) - 0.5) for value in transition_values
        )
        candidate["energy_blocking_positive_count"] = (
            candidate["energy_diagnostics"]["energy_blocked_positive_count"]
            if candidate["energy_diagnostics"] else None
        )
        candidates.append(candidate)
    transition_candidates = [
        candidate for candidate in candidates
        if candidate["all_required_u_usable"]
        and not candidate["is_saturation"]
        and candidate["usable"]
        and candidate["transition_N_T"] >= 2
        and candidate["energy_diagnostics"]
        and candidate["energy_diagnostics"]["energy_blocking_complete"]
        and candidate["energy_blocking_positive_count"] > 0
    ]
    if not transition_candidates:
        return {
            "status": "PAIRED_CAL_BLOCKED", "selection": None,
            "threshold_policy": _paired_policy_payload(policy),
            "saturation_condition": matrix["saturation_condition"],
            "candidates": candidates, "diagnostics": diagnostics, "matrix": matrix,
        }
    selected_transition = min(
        transition_candidates,
        key=lambda candidate: (
            -candidate["transition_N_T"], candidate["transition_deviation"],
            abs(Fraction(candidate["eta"]) - 1), Fraction(candidate["kappa"]),
            Fraction(candidate["eta"]),
        ),
    )
    selected_kappa = selected_transition["kappa"]
    selected_eta = Fraction(selected_transition["eta"])
    same_kappa = [candidate for candidate in candidates if candidate["kappa"] == selected_kappa]
    low = [
        candidate for candidate in same_kappa
        if Fraction(candidate["eta"]) < selected_eta
        and not candidate["is_saturation"]
        and candidate["usable"]
        and candidate["energy_diagnostics"]
        and candidate["energy_diagnostics"]["energy_blocking_complete"]
        and candidate["retention"] is not None
        and Fraction(str(candidate["retention"])) <= policy.low_max_retention
    ]
    high = [
        candidate for candidate in same_kappa
        if Fraction(candidate["eta"]) > selected_eta
        and not candidate["is_saturation"]
        and candidate["usable"]
        and candidate["energy_diagnostics"]
        and candidate["energy_diagnostics"]["energy_blocking_complete"]
        and candidate["energy_blocking_positive_count"] == 0
        and candidate["retention"] is not None
        and Fraction(str(candidate["retention"])) >= policy.high_min_retention
    ]
    low = max(low, key=lambda candidate: Fraction(candidate["eta"]), default=None)
    high = min(high, key=lambda candidate: Fraction(candidate["eta"]), default=None)
    selection = {
        "kappa_star": selected_kappa,
        "eta_low": low["eta"] if low else None,
        "eta_transition": selected_transition["eta"],
        "eta_high": high["eta"] if high else None,
        "LOW": {"kappa": selected_kappa, "eta": low["eta"]} if low else None,
        "TRANSITION": {"kappa": selected_kappa, "eta": selected_transition["eta"]},
        "HIGH": {"kappa": selected_kappa, "eta": high["eta"]} if high else None,
    }
    if low is None and high is None:
        status = "NEEDS_PAIRED_EXTENSION_BOTH"
    elif low is None:
        status = "NEEDS_PAIRED_EXTENSION_LOW"
    elif high is None:
        status = "NEEDS_PAIRED_EXTENSION_HIGH"
    else:
        status = "PAIRED_SELECTION_OK"
    return {
        "status": status, "selection": selection,
        "threshold_policy": _paired_policy_payload(policy),
        "saturation_condition": matrix["saturation_condition"],
        "candidates": candidates, "diagnostics": diagnostics, "matrix": matrix,
    }


__all__ = [
    "CAL_ETAS", "CAL_KAPPAS", "CAL_SATURATION_CONDITION", "CAL_SCHEDULERS", "CAL_UTILIZATIONS",
    "FORMAL_HORIZON_MS", "FORMAL_SCHEDULERS", "FORMAL_TASKSETS_PER_UTILIZATION",
    "FORMAL_UTILIZATIONS", "PairedRetentionPolicy", "PerfGError", "SMOKE_CONDITIONS", "SCHEDULER_CLI",
    "build_raw_trace", "cal_confirmation_conditions", "cal_confirmation_plan", "cal_plan",
    "condition", "energy_material", "formal_plan",
    "materialize_tasksets", "q_matrix", "q_only_projection", "request_id",
    "paired_retention_matrix", "paired_saturation_diagnostics",
    "select_calibration_paired", "select_three_conditions", "select_transition",
    "taskset_key", "validate_pairing",
]
