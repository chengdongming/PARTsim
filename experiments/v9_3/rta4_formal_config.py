"""Opt-in configuration contract for the v9.3 four-level formal pipeline.

This module is deliberately independent of :mod:`experiments.v9_3.config`.
The legacy loader and its semantic hashes therefore remain byte-for-byte
unchanged unless a caller explicitly selects this profile.
"""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

import asap_block_rta_v9_3_methods as method_registry

from . import exact_energy
from .constrained_taskset_identity import (
    CONSTRAINED_UNIFORM_SLACK_MODE,
    GENERATION_REQUEST_CONTRACT_VERSION,
    TASKSET_IDENTITY_CONTRACT_VERSION,
)
from .release_applicability import (
    ASYNC_HASH_PHASE_V1,
    RELEASE_HORIZON,
    RELEASE_PROJECTION_CONTRACT_VERSION,
    SIMULATION_APPLICABILITY_CONTRACT_VERSION,
    SYNC_V1,
)


RTA4_FORMAL_PROFILE = "ASAP_BLOCK_V9_3_RTA4_FORMAL_V1"
RTA4_FORMAL_PLAN_VERSION = "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V1"
RTA4_FORMAL_SCHEMA_VERSION = "ASAP_BLOCK_V9_3_RTA4_FORMAL_SCHEMA_V1"
RTA4_FORMAL_PARAMETER_STATUS = "UNAUTHORIZED_PRE_PILOT"
RTA4_FORMAL_STORE_VERSION = "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V1"
RTA4_FORMAL_TEMPLATE_VERSION = "ASAP_BLOCK_V9_3_RTA4_PRE_PILOT_TEMPLATE_V1"
RTA4_FORMAL_CONFIG_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v1"
RTA4_METHOD_REGISTRY_DOMAIN = "ASAP_BLOCK:V9.3:RTA4_METHOD_REGISTRY:v1"

RTA4_CORES = (
    "CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B",
)
RTA4_RECURSIVE_METHODS = (
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
RTA4_CORE2_METHODS = (
    "CW_D", "LOC_D", "PH_D", "SEQ_D", "CW_THETA_CW", "SEQ_THETA_SEQ",
)


class RTA4FormalConfigError(ValueError):
    """Raised when the new profile is incomplete or not exactly frozen."""


def fraction_text(value: Fraction) -> str:
    if type(value) is not Fraction:
        raise RTA4FormalConfigError("exact values must be Fraction objects")
    return str(value.numerator) if value.denominator == 1 else (
        f"{value.numerator}/{value.denominator}"
    )


def exact_fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise RTA4FormalConfigError(f"{label} must be exact rational data")
    if isinstance(value, Fraction):
        result = value
    elif type(value) in {int, str}:
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise RTA4FormalConfigError(f"invalid exact value for {label}") from exc
    else:
        raise RTA4FormalConfigError(f"{label} must be exact rational data")
    return result


def _canonical(value: Any) -> Any:
    if isinstance(value, Fraction):
        return fraction_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        raise RTA4FormalConfigError("formal configuration forbids binary floats")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    ).hexdigest()


def method_registry_material() -> list[Dict[str, Any]]:
    """Materialize metadata from the sole unified eight-method registry."""

    return [
        {
            "method_id": spec.method_id.value,
            "kernel": spec.kernel.value,
            "carry_policy": spec.carry_policy.value,
            "dominance_rank": spec.dominance_rank,
            "is_final_recursive_method": spec.is_final_recursive_method,
            "is_ablation_method": spec.is_ablation_method,
        }
        for spec in method_registry.V93_METHOD_SPECS
    ]


def method_registry_identity() -> str:
    return domain_hash(RTA4_METHOD_REGISTRY_DOMAIN, method_registry_material())


def _common_contract() -> Dict[str, Any]:
    return {
        "profile": RTA4_FORMAL_PROFILE,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION,
        "plan_version": RTA4_FORMAL_PLAN_VERSION,
        "parameter_status": RTA4_FORMAL_PARAMETER_STATUS,
    }


def _common_generation() -> Dict[str, Any]:
    return {
        "formal_master_seed": 930612,
        "period_min": 40,
        "period_max": 200,
        "priority_policy": "RM",
        "wcet_rounding": "compensated",
        "utilization_allocation_mode": "frozen_v9_3_generator_v1",
        "minimum_task_utilization": "1/100",
        "maximum_task_utilization": "4/5",
        "utilization_tolerance": "1/100",
        "deadline_mode": CONSTRAINED_UNIFORM_SLACK_MODE,
        "power_generation_mode": "generator_default_heterogeneous",
        "generator_version": "ASAP_BLOCK_V9_3_GENERATOR_V1",
        "generation_request_contract": GENERATION_REQUEST_CONTRACT_VERSION,
        "taskset_identity_contract": TASKSET_IDENTITY_CONTRACT_VERSION,
    }


def _plan_parameters(core: str) -> Dict[str, Any]:
    if core == "CORE-1":
        return {
            "processors": 4, "task_count": 10,
            "normalized_utilization": [
                "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5",
            ],
            "tasksets_per_utilization": 200,
            "e0": ["0", "1/20", "1"],
            "methods": list(RTA4_RECURSIVE_METHODS),
        }
    if core == "CORE-2":
        return {
            "source_core": "CORE-1", "reused_tasksets": 1600,
            "e0": ["0", "1/20", "1"],
            "methods": list(RTA4_CORE2_METHODS),
            "referenced_recursive_methods": ["LOC_THETA_LOC", "PH_THETA_PH"],
        }
    if core == "CORE-3":
        return {
            "source_core": "CORE-1", "new_rta_requests": 0,
            "tasksets": 1600, "release_horizon": RELEASE_HORIZON,
            "observation_horizon": "release_horizon_plus_dmax",
            "theorem_release_modes": [ASYNC_HASH_PHASE_V1, SYNC_V1],
            "finite_battery_release_mode": ASYNC_HASH_PHASE_V1,
            "finite_battery_capacities": ["20", "100"],
            "projection_methods": list(RTA4_RECURSIVE_METHODS),
            "projection_e0": ["0", "1/20", "1"],
        }
    if core == "CORE-4":
        return {
            "processors": 4, "task_count": 10,
            "normalized_utilization": ["3/10", "2/5", "1/2", "3/5", "7/10"],
            "skeletons_per_utilization": 200,
            "baseline": {
                "e0": "1/20", "service_scale": "1",
                "power_scale": "1", "deadline_slack_fraction": "3/4",
            },
            "axes": {
                "e0": ["0", "1/100", "1/50", "3/100", "1/20", "1/5", "1"],
                "service_scale": ["1/2", "3/4", "1", "5/4", "3/2"],
                "power_scale": ["1/2", "3/4", "1", "5/4", "3/2"],
                "deadline_slack_fraction": ["1/4", "1/2", "3/4", "1"],
            },
            "methods": list(RTA4_RECURSIVE_METHODS),
            "design": "ONE_FACTOR_AT_A_TIME",
        }
    if core == "CORE-5A":
        return {
            "baseline": {
                "e0": "1/20", "normalized_utilization": "1/2",
                "service_scale": "1", "power_scale": "1",
                "deadline_slack_fraction": "3/4",
            },
            "task_count_axis": {"values": [5, 10, 20, 30], "processors": 4, "tasksets": 100},
            "processor_axis": {"values": [2, 4, 8], "task_count": 10, "tasksets": 100},
            "integer_time_scale_axis": {"values": [1, 2, 4, 8], "base_tasksets": 100},
            "methods": list(RTA4_RECURSIVE_METHODS), "worker_count": 1,
        }
    if core == "CORE-5B":
        return {
            "source_core": "CORE-4", "candidate_pool": "CORE4_BASELINE",
            "utilization_strata": ["3/10", "2/5", "1/2", "3/5", "7/10"],
            "candidates_per_method_stratum": 200,
            "selected_per_method_stratum": 150,
            "methods": list(RTA4_RECURSIVE_METHODS),
            "workers": [1, 2, 4, 8],
            "selection_rule": "DOMAIN_HASH_ORDERED_RESULT_INDEPENDENT_V1",
        }
    raise RTA4FormalConfigError(f"unknown RTA4 formal core: {core!r}")


def default_rta4_formal_config(core: str) -> Dict[str, Any]:
    if core not in RTA4_CORES:
        raise RTA4FormalConfigError(f"unknown RTA4 formal core: {core!r}")
    slug = core.lower().replace("-", "")
    return {
        "experiment_id": f"asap-block-v9.3-rta4-{slug}-unauthorized-pre-pilot-v1",
        "core": core,
        "experiment_contract": _common_contract(),
        "generation": _common_generation(),
        "plan": _plan_parameters(core),
        "identity": {
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "numeric_contract_sha256": exact_energy.NUMERIC_CONTRACT_SHA256,
            "method_registry_identity": method_registry_identity(),
            "release_projection_contract": RELEASE_PROJECTION_CONTRACT_VERSION,
            "simulation_applicability_contract": SIMULATION_APPLICABILITY_CONTRACT_VERSION,
            "taskset_store_version": RTA4_FORMAL_STORE_VERSION,
        },
        "statistics": {
            "bootstrap_cluster": "taskset_skeleton_id",
            "bootstrap_replicates": 10_000,
            "bootstrap_seed": 930612,
            "confidence_level": "19/20",
        },
        "execution": {
            "mode": "FORMAL",
            "output_root": f"results/v9_3_rta4_{slug}_formal_v1",
            "taskset_store": "results/v9_3_rta4_formal_tasksets_v1",
            "timeout_contract": "UNFROZEN_PRE_PILOT",
            "resume": False,
            "fail_fast_on_p0": True,
            "preserve_attempt_history": True,
        },
    }


def _require_exact_mapping(actual: Any, expected: Mapping[str, Any], label: str) -> None:
    if not isinstance(actual, Mapping):
        raise RTA4FormalConfigError(f"{label} must be a mapping")
    if _canonical(actual) != _canonical(expected):
        raise RTA4FormalConfigError(f"{label} does not match the frozen {RTA4_FORMAL_PROFILE} contract")


def validate_rta4_formal_config(
    raw: Mapping[str, Any], *, expected_core: str | None = None,
) -> Dict[str, Any]:
    """Validate only the additive profile; no legacy loader is involved."""

    if not isinstance(raw, Mapping):
        raise RTA4FormalConfigError("formal configuration must be a mapping")
    core = raw.get("core")
    if core not in RTA4_CORES:
        raise RTA4FormalConfigError("core is not a v9.3 RTA4 formal core")
    if expected_core is not None and core != expected_core:
        raise RTA4FormalConfigError(f"runner requires {expected_core}, got {core!r}")
    expected = default_rta4_formal_config(str(core))
    allowed_top = set(expected)
    extra = set(raw) - allowed_top
    if extra:
        raise RTA4FormalConfigError(f"unexpected formal configuration fields: {sorted(extra)}")
    for key in ("experiment_id", "core", "experiment_contract", "generation", "plan", "identity", "statistics"):
        if key not in raw:
            raise RTA4FormalConfigError(f"missing formal configuration field: {key}")
    if raw["experiment_id"] != expected["experiment_id"]:
        raise RTA4FormalConfigError("experiment_id does not match the frozen pre-pilot template")
    for key in ("experiment_contract", "generation", "plan", "identity", "statistics"):
        _require_exact_mapping(raw[key], expected[key], key)
    execution = raw.get("execution")
    if not isinstance(execution, Mapping):
        raise RTA4FormalConfigError("execution must be a mapping")
    required_execution = {
        "mode", "output_root", "taskset_store", "timeout_contract", "resume",
        "fail_fast_on_p0", "preserve_attempt_history",
    }
    if set(execution) != required_execution:
        raise RTA4FormalConfigError("execution does not have its exact field set")
    for key in ("mode", "timeout_contract", "fail_fast_on_p0", "preserve_attempt_history"):
        if execution[key] != expected["execution"][key]:
            raise RTA4FormalConfigError(f"execution.{key} violates the pre-pilot contract")
    if type(execution["resume"]) is not bool:
        raise RTA4FormalConfigError("execution.resume must be a strict boolean")
    for key in ("output_root", "taskset_store"):
        if not isinstance(execution[key], str) or not execution[key].strip():
            raise RTA4FormalConfigError(f"execution.{key} must be a non-empty path")
    return deepcopy(dict(raw))


def load_rta4_formal_config(
    path: Path | str, *, expected_core: str | None = None,
) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RTA4FormalConfigError(f"cannot load formal configuration: {path}") from exc
    if isinstance(raw, Mapping) and raw.get("template") == RTA4_FORMAL_TEMPLATE_VERSION:
        allowed = {"template", "core", "experiment_contract", "execution"}
        if set(raw) - allowed:
            raise RTA4FormalConfigError("compact template contains unexpected fields")
        core = raw.get("core")
        expanded = default_rta4_formal_config(str(core))
        _require_exact_mapping(
            raw.get("experiment_contract"), expanded["experiment_contract"],
            "experiment_contract",
        )
        execution = raw.get("execution", {})
        if not isinstance(execution, Mapping):
            raise RTA4FormalConfigError("execution must be a mapping")
        for key in ("output_root", "taskset_store"):
            if key in execution:
                expanded["execution"][key] = execution[key]
        raw = expanded
    return validate_rta4_formal_config(raw, expected_core=expected_core)


def rta4_formal_config_hash(config: Mapping[str, Any]) -> str:
    normalized = validate_rta4_formal_config(config)
    semantic = deepcopy(normalized)
    semantic["execution"].pop("output_root")
    semantic["execution"].pop("taskset_store")
    semantic["execution"].pop("resume")
    return domain_hash(RTA4_FORMAL_CONFIG_DOMAIN, semantic)


__all__ = [
    "RTA4_CORE2_METHODS", "RTA4_CORES", "RTA4_FORMAL_CONFIG_DOMAIN",
    "RTA4_FORMAL_PARAMETER_STATUS", "RTA4_FORMAL_PLAN_VERSION",
    "RTA4_FORMAL_PROFILE", "RTA4_FORMAL_SCHEMA_VERSION",
    "RTA4_FORMAL_STORE_VERSION", "RTA4_FORMAL_TEMPLATE_VERSION",
    "RTA4_RECURSIVE_METHODS",
    "RTA4FormalConfigError", "canonical_json", "default_rta4_formal_config",
    "domain_hash", "exact_fraction", "fraction_text",
    "load_rta4_formal_config", "method_registry_identity",
    "method_registry_material", "rta4_formal_config_hash",
    "validate_rta4_formal_config",
]
