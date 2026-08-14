"""Minimal deterministic planning and execution support for PERF-G.

This module owns only the PERF-G scientific contract.  Task generation and
the simulator remain the existing v9.3 implementations; this file supplies
their inputs, paired request identities, and the Q-only calibration rules.
"""

from __future__ import annotations

from dataclasses import asdict
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
FORMAL_TASKSETS_PER_UTILIZATION = 200
CAL_UTILIZATIONS = (Fraction("3/10"), Fraction("1/2"), Fraction("7/10"))
CAL_TASKSETS_PER_UTILIZATION = 30
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
BASE_SYSTEM_TEMPLATE = "v9_3_b4_priority_energy_system_template.yml"


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
        for eta in sorted({key[1] for key in q}, key=Fraction):
            values = [float(q[(kappa, eta, fraction_text(u))]) for u in utilizations]
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
    lower = [eta for eta in eta_values if eta < eta_t and q.get((kappa, fraction_text(eta), "1/2"), 1.0) <= 0.2]
    higher = [eta for eta in eta_values if eta > eta_t and q.get((kappa, fraction_text(eta), "1/2"), 0.0) >= 0.8]
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


__all__ = [
    "CAL_ETAS", "CAL_KAPPAS", "CAL_SCHEDULERS", "CAL_UTILIZATIONS",
    "FORMAL_HORIZON_MS", "FORMAL_SCHEDULERS", "FORMAL_TASKSETS_PER_UTILIZATION",
    "FORMAL_UTILIZATIONS", "PerfGError", "SMOKE_CONDITIONS", "SCHEDULER_CLI",
    "build_raw_trace", "cal_plan", "condition", "energy_material", "formal_plan",
    "materialize_tasksets", "q_matrix", "q_only_projection", "request_id",
    "select_three_conditions", "select_transition", "taskset_key", "validate_pairing",
]
