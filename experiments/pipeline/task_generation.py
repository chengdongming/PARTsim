"""Common task-generation defaults and exact energy materialization."""

from __future__ import annotations


from fractions import Fraction





from typing import Any, Mapping, Sequence

from .config import fraction_text, validate_config


from .taskset_store import StoredTaskset

PROCESSORS = 4

TASK_COUNT = 10

FORMAL_HORIZON_MS = 60000

FORMAL_TIMEOUT_SECONDS = 300

PERIOD_MIN_MS = 40

PERIOD_MAX_MS = 200

UTILIZATION_TOLERANCE = Fraction("1/100")

MIN_TASK_UTILIZATION = Fraction("1/100")

MAX_TASK_UTILIZATION = Fraction("4/5")

WORKLOADS = ("bzip2", "control", "decrypt", "encrypt", "hash")

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

CAL_SEED = 410731

FORMAL_SEED = 910427

SMOKE_SEED = 710213

BASE_SYSTEM_TEMPLATE = "v9_3_b4_priority_energy_system_template.yml"

class PerfGError(ValueError):
    """Raised when a PERF-G plan or result violates its frozen contract."""

def _as_fraction(value: Any, label: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise PerfGError(f"{label} is not an exact rational") from exc
    return result

def _task_generation_config(
    namespace: str, utilizations: Sequence[Fraction], count: int, *,
    system_template: str = BASE_SYSTEM_TEMPLATE,
) -> dict[str, Any]:
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
                "system_template": system_template, "solar_scale": "1",
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
