"""Strict external campaign configuration for parameterized RTA4 V3 plans.

V1 and V2 remain frozen in their existing modules.  This module accepts only
the finite scientific axes explicitly listed for each RTA4 core and separates
them from operational settings before either identity is computed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
from pathlib import Path
import re
from typing import Any, Dict, Mapping, Sequence

import yaml

from . import exact_energy
from .rta4_formal_config import canonical_json, domain_hash, fraction_text
from .rta4_numeric_contract_v2 import RTA4_NUMERIC_CONTRACT_V2_SHA256


RTA4_FORMAL_PROFILE_V3 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_V3_PARAMETERIZED_SHARED_ENERGY"
)
RTA4_FORMAL_SCHEMA_VERSION_V3 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_SCHEMA_V3_PARAMETERIZED"
)
RTA4_FORMAL_PLAN_VERSION_V3 = (
    "ASAP_BLOCK_V9_3_RTA4_FORMAL_PLAN_V3_PARAMETERIZED"
)
RTA4_FORMAL_CONFIG_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_FORMAL_CONFIG:v3"
RTA4_FORMAL_RAW_CAMPAIGN_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_RAW_CAMPAIGN:v3"
RTA4_FORMAL_TASKSET_STORE_DOMAIN_V3 = "ASAP_BLOCK:V9.3:RTA4_TASKSET_STORE:v3"
RTA4_CORES_V3 = ("CORE-1", "CORE-2", "CORE-3", "CORE-4", "CORE-5A", "CORE-5B")
RTA4_RECURSIVE_METHODS_V3 = (
    "CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ",
)
RTA4_CORE2_METHODS_V3 = (
    "CW_D", "LOC_D", "PH_D", "SEQ_D", "CW_THETA_CW", "SEQ_THETA_SEQ",
)
RTA4_RELEASE_MODES_V3 = ("ASYNC_HASH_PHASE_V1", "SYNC_V1")
RTA4_SELECTION_RULE_V3 = "DOMAIN_HASH_ORDERED_RESULT_INDEPENDENT_V1"
RTA4_TASKSET_FIRST_SELECTION_RULE_V3 = (
    "DOMAIN_HASH_ORDERED_TASKSET_FIRST_RESULT_INDEPENDENT_V1"
)

_COMMON_FIELDS = {"campaign_id", "core", "runtime"}
_CORE_FIELDS = {
    "CORE-1": {
        "processors", "task_count", "normalized_utilization",
        "tasksets_per_utilization", "e0", "methods",
    },
    "CORE-2": {"source", "e0", "methods", "referenced_recursive_methods"},
    "CORE-3": {
        "source", "release_modes", "finite_battery_capacities",
        "projection_methods", "projection_e0", "simulation_horizon",
    },
    "CORE-4": {
        "processors", "task_count", "normalized_utilization",
        "skeletons_per_utilization", "baseline", "axes", "methods",
    },
    "CORE-5A": {
        "baseline", "task_count_axis", "processor_axis",
        "integer_time_scale_axis", "methods",
    },
    "CORE-5B": {
        "source", "utilization_strata", "candidates_per_method_stratum",
        "selected_per_method_stratum", "methods", "workers",
    },
}
_RUNTIME_FIELDS = {
    "output_root", "taskset_store", "log_path", "resume", "worker_count",
    "max_in_flight", "timeout_seconds", "max_records", "source_taskset_store",
    "checkpoint_every_records", "checkpoint_every_seconds",
}
_SOURCE_FIELDS = {
    "core", "source_scope", "source_campaign_config_sha256",
    "source_plan_sha256", "source_taskset_store_identity", "taskset_count",
}
_CORE4_BASELINE_FIELDS = {
    "e0", "service_scale", "power_scale", "deadline_slack_fraction",
}
_CORE5A_BASELINE_FIELDS = {
    "e0", "normalized_utilization", "service_scale", "power_scale",
    "deadline_slack_fraction",
}


class RTA4FormalConfigV3Error(ValueError):
    """Raised when a parameterized campaign is not finite and exact."""


@dataclass(frozen=True)
class LoadedCampaignV3:
    campaign_path: Path
    raw_campaign_file_sha256: str
    normalized_scientific_config: Mapping[str, Any]
    normalized_scientific_config_sha256: str
    runtime: Mapping[str, Any]


def _exact_text(value: Any, label: str, *, minimum: Fraction | None = None,
                maximum: Fraction | None = None,
                strict_minimum: bool = False) -> str:
    if type(value) is not str or not value.strip():
        raise RTA4FormalConfigV3Error(f"{label} must be an exact rational string")
    try:
        exact = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise RTA4FormalConfigV3Error(f"{label} is not an exact rational") from exc
    if minimum is not None and exact < minimum:
        raise RTA4FormalConfigV3Error(f"{label} is below its allowed range")
    if minimum is not None and strict_minimum and exact == minimum:
        raise RTA4FormalConfigV3Error(f"{label} must be greater than its minimum")
    if maximum is not None and exact > maximum:
        raise RTA4FormalConfigV3Error(f"{label} is above its allowed range")
    return fraction_text(exact)


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise RTA4FormalConfigV3Error(f"{label} must be a positive integer")
    return value


def _sha(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise RTA4FormalConfigV3Error(f"{label} must be a lowercase SHA-256")
    return value


def _mapping(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RTA4FormalConfigV3Error(f"{label} field set mismatch")
    return value


def _exact_axis(value: Any, label: str, *, minimum: Fraction,
                maximum: Fraction | None = None,
                strict_minimum: bool = False) -> list[str]:
    if type(value) is not list or not value:
        raise RTA4FormalConfigV3Error(f"{label} must be a non-empty list")
    result = [
        _exact_text(
            item, f"{label}[{index}]", minimum=minimum, maximum=maximum,
            strict_minimum=strict_minimum,
        )
        for index, item in enumerate(value)
    ]
    if len(set(result)) != len(result):
        raise RTA4FormalConfigV3Error(f"{label} contains duplicates")
    return result


def _integer_axis(value: Any, label: str) -> list[int]:
    if type(value) is not list or not value:
        raise RTA4FormalConfigV3Error(f"{label} must be a non-empty list")
    result = [_positive_int(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise RTA4FormalConfigV3Error(f"{label} contains duplicates")
    return result


def _methods(value: Any, label: str, allowlist: Sequence[str]) -> list[str]:
    if type(value) is not list or not value:
        raise RTA4FormalConfigV3Error(f"{label} must be a non-empty list")
    if any(type(item) is not str for item in value):
        raise RTA4FormalConfigV3Error(f"{label} entries must be method identifiers")
    if len(set(value)) != len(value):
        raise RTA4FormalConfigV3Error(f"{label} contains duplicate methods")
    unknown = sorted(set(value).difference(allowlist))
    if unknown:
        raise RTA4FormalConfigV3Error(f"{label} contains unknown methods: {unknown}")
    selected = set(value)
    return [method for method in allowlist if method in selected]


def _source(value: Any, expected_core: str) -> Dict[str, Any]:
    source = _mapping(value, _SOURCE_FIELDS, "source")
    expected_scope = (
        "CORE4_BASELINE" if expected_core == "CORE-4" else "CORE1_TASKSET_STORE"
    )
    if source["core"] != expected_core or source["source_scope"] != expected_scope:
        raise RTA4FormalConfigV3Error(f"source must bind {expected_core} {expected_scope}")
    return {
        "core": expected_core,
        "source_scope": expected_scope,
        "source_campaign_config_sha256": _sha(
            source["source_campaign_config_sha256"], "source campaign config identity",
        ),
        "source_plan_sha256": _sha(source["source_plan_sha256"], "source plan identity"),
        "source_taskset_store_identity": _sha(
            source["source_taskset_store_identity"], "source taskset store identity",
        ),
        "taskset_count": _positive_int(source["taskset_count"], "source taskset_count"),
    }


def _baseline(value: Any, *, include_utilization: bool) -> Dict[str, str]:
    fields = _CORE5A_BASELINE_FIELDS if include_utilization else _CORE4_BASELINE_FIELDS
    row = _mapping(value, fields, "baseline")
    result = {
        "e0": _exact_text(row["e0"], "baseline.e0", minimum=Fraction(0)),
        "service_scale": _exact_text(
            row["service_scale"], "baseline.service_scale", minimum=Fraction(0),
            strict_minimum=True,
        ),
        "power_scale": _exact_text(
            row["power_scale"], "baseline.power_scale", minimum=Fraction(0),
            strict_minimum=True,
        ),
        "deadline_slack_fraction": _exact_text(
            row["deadline_slack_fraction"], "baseline.deadline_slack_fraction",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        ),
    }
    if include_utilization:
        result["normalized_utilization"] = _exact_text(
            row["normalized_utilization"], "baseline.normalized_utilization",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        )
    return result


def _runtime(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value).difference(_RUNTIME_FIELDS):
        raise RTA4FormalConfigV3Error("runtime contains unknown fields")
    result: Dict[str, Any] = {}
    for key in (
        "output_root", "taskset_store", "source_taskset_store", "log_path",
    ):
        if key in value:
            if type(value[key]) is not str or not value[key].strip():
                raise RTA4FormalConfigV3Error(f"runtime.{key} must be a non-empty path")
            result[key] = value[key]
    if "resume" in value:
        if type(value["resume"]) is not bool:
            raise RTA4FormalConfigV3Error("runtime.resume must be a strict boolean")
        result["resume"] = value["resume"]
    for key in ("worker_count", "max_in_flight", "timeout_seconds"):
        if key in value:
            result[key] = _positive_int(value[key], f"runtime.{key}")
    if "max_records" in value:
        if type(value["max_records"]) is not int or value["max_records"] < 0:
            raise RTA4FormalConfigV3Error("runtime.max_records must be non-negative")
        result["max_records"] = value["max_records"]
    if ("worker_count" in result and "max_in_flight" in result
            and result["max_in_flight"] < result["worker_count"]):
        raise RTA4FormalConfigV3Error("runtime.max_in_flight must cover worker_count")
    return result


def _normalize_core1(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "processors": _positive_int(raw["processors"], "processors"),
        "task_count": _positive_int(raw["task_count"], "task_count"),
        "normalized_utilization": _exact_axis(
            raw["normalized_utilization"], "normalized_utilization",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        ),
        "tasksets_per_utilization": _positive_int(
            raw["tasksets_per_utilization"], "tasksets_per_utilization",
        ),
        "e0": _exact_axis(raw["e0"], "e0", minimum=Fraction(0)),
        "methods": _methods(raw["methods"], "methods", RTA4_RECURSIVE_METHODS_V3),
    }


def _normalize_core2(raw: Mapping[str, Any]) -> Dict[str, Any]:
    referenced = _methods(
        raw["referenced_recursive_methods"], "referenced_recursive_methods",
        RTA4_RECURSIVE_METHODS_V3,
    )
    return {
        "source": _source(raw["source"], "CORE-1"),
        "e0": _exact_axis(raw["e0"], "e0", minimum=Fraction(0)),
        "methods": _methods(raw["methods"], "methods", RTA4_CORE2_METHODS_V3),
        "referenced_recursive_methods": referenced,
    }


def _normalize_core3(raw: Mapping[str, Any]) -> Dict[str, Any]:
    modes = raw["release_modes"]
    if type(modes) is not list or not modes or len(set(modes)) != len(modes):
        raise RTA4FormalConfigV3Error("release_modes must be non-empty and unique")
    if any(mode not in RTA4_RELEASE_MODES_V3 for mode in modes):
        raise RTA4FormalConfigV3Error("release_modes contains unsupported semantics")
    horizon = _mapping(
        raw["simulation_horizon"], {"release_horizon", "observation_horizon"},
        "simulation_horizon",
    )
    if horizon["observation_horizon"] != "release_horizon_plus_dmax":
        raise RTA4FormalConfigV3Error("unsupported observation horizon semantics")
    return {
        "source": _source(raw["source"], "CORE-1"),
        "release_modes": [mode for mode in RTA4_RELEASE_MODES_V3 if mode in modes],
        "finite_battery_capacities": _exact_axis(
            raw["finite_battery_capacities"], "finite_battery_capacities",
            minimum=Fraction(1),
        ),
        "projection_methods": _methods(
            raw["projection_methods"], "projection_methods", RTA4_RECURSIVE_METHODS_V3,
        ),
        "projection_e0": _exact_axis(
            raw["projection_e0"], "projection_e0", minimum=Fraction(0),
        ),
        "simulation_horizon": {
            "release_horizon": _positive_int(
                horizon["release_horizon"], "simulation_horizon.release_horizon",
            ),
            "observation_horizon": "release_horizon_plus_dmax",
        },
    }


def _normalize_core4(raw: Mapping[str, Any]) -> Dict[str, Any]:
    baseline = _baseline(raw["baseline"], include_utilization=False)
    axes = _mapping(raw["axes"], {
        "e0", "service_scale", "power_scale", "deadline_slack_fraction",
    }, "axes")
    normalized_axes = {
        "e0": _exact_axis(axes["e0"], "axes.e0", minimum=Fraction(0)),
        "service_scale": _exact_axis(
            axes["service_scale"], "axes.service_scale", minimum=Fraction(0),
            strict_minimum=True,
        ),
        "power_scale": _exact_axis(
            axes["power_scale"], "axes.power_scale", minimum=Fraction(0),
            strict_minimum=True,
        ),
        "deadline_slack_fraction": _exact_axis(
            axes["deadline_slack_fraction"], "axes.deadline_slack_fraction",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        ),
    }
    return {
        "processors": _positive_int(raw["processors"], "processors"),
        "task_count": _positive_int(raw["task_count"], "task_count"),
        "normalized_utilization": _exact_axis(
            raw["normalized_utilization"], "normalized_utilization",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        ),
        "skeletons_per_utilization": _positive_int(
            raw["skeletons_per_utilization"], "skeletons_per_utilization",
        ),
        "baseline": baseline,
        "axes": normalized_axes,
        "methods": _methods(raw["methods"], "methods", RTA4_RECURSIVE_METHODS_V3),
        "design": "ONE_FACTOR_AT_A_TIME",
    }


def _normalize_core5a(raw: Mapping[str, Any]) -> Dict[str, Any]:
    task_axis = _mapping(
        raw["task_count_axis"], {"values", "processors", "tasksets"},
        "task_count_axis",
    )
    processor_axis_fields = {"values", "task_count", "tasksets"}
    processor_axis_raw = raw["processor_axis"]
    processor_axis_actual = (
        set(processor_axis_raw) if isinstance(processor_axis_raw, Mapping) else set()
    )
    if (
        not isinstance(processor_axis_raw, Mapping)
        or processor_axis_actual not in (
            processor_axis_fields,
            processor_axis_fields | {"fixed_total_utilization"},
            processor_axis_fields | {
                "fixed_total_utilization",
                "fixed_total_utilization_tolerance",
            },
        )
    ):
        raise RTA4FormalConfigV3Error("processor_axis field set mismatch")
    processor_axis = processor_axis_raw
    time_axis = _mapping(
        raw["integer_time_scale_axis"], {"values", "base_tasksets"},
        "integer_time_scale_axis",
    )
    processor_values = _integer_axis(
        processor_axis["values"], "processor_axis.values",
    )
    normalized_processor_axis: Dict[str, Any] = {
        "values": processor_values,
        "task_count": _positive_int(
            processor_axis["task_count"], "processor_axis.task_count",
        ),
        "tasksets": _positive_int(
            processor_axis["tasksets"], "processor_axis.tasksets",
        ),
    }
    if "fixed_total_utilization" in processor_axis:
        fixed_total = _exact_text(
            processor_axis["fixed_total_utilization"],
            "processor_axis.fixed_total_utilization",
            minimum=Fraction(0), strict_minimum=True,
        )
        if any(Fraction(fixed_total) / value > 1 for value in processor_values):
            raise RTA4FormalConfigV3Error(
                "processor_axis fixed_total_utilization exceeds processor capacity"
            )
        normalized_processor_axis["fixed_total_utilization"] = fixed_total
    if "fixed_total_utilization_tolerance" in processor_axis:
        tolerance = _exact_text(
            processor_axis["fixed_total_utilization_tolerance"],
            "processor_axis.fixed_total_utilization_tolerance",
            minimum=Fraction(0),
        )
        if tolerance != processor_axis["fixed_total_utilization_tolerance"]:
            raise RTA4FormalConfigV3Error(
                "processor_axis.fixed_total_utilization_tolerance must be a "
                f"canonical exact rational string: {tolerance}"
            )
        normalized_processor_axis[
            "fixed_total_utilization_tolerance"
        ] = tolerance
    return {
        "baseline": _baseline(raw["baseline"], include_utilization=True),
        "task_count_axis": {
            "values": _integer_axis(task_axis["values"], "task_count_axis.values"),
            "processors": _positive_int(task_axis["processors"], "task_count_axis.processors"),
            "tasksets": _positive_int(task_axis["tasksets"], "task_count_axis.tasksets"),
        },
        "processor_axis": normalized_processor_axis,
        "integer_time_scale_axis": {
            "values": _integer_axis(time_axis["values"], "integer_time_scale_axis.values"),
            "base_tasksets": _positive_int(time_axis["base_tasksets"], "integer_time_scale_axis.base_tasksets"),
        },
        "methods": _methods(raw["methods"], "methods", RTA4_RECURSIVE_METHODS_V3),
    }


def _normalize_core5b(raw: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = _positive_int(
        raw["candidates_per_method_stratum"], "candidates_per_method_stratum",
    )
    selected = _positive_int(
        raw["selected_per_method_stratum"], "selected_per_method_stratum",
    )
    if selected > candidates:
        raise RTA4FormalConfigV3Error("selected_per_method_stratum exceeds candidates")
    source_raw = raw["source"]
    if not isinstance(source_raw, Mapping):
        raise RTA4FormalConfigV3Error("source field set mismatch")
    source_core = source_raw.get("core")
    if source_core not in {"CORE-1", "CORE-4"}:
        raise RTA4FormalConfigV3Error(
            "CORE-5B source must bind CORE-1 CORE1_TASKSET_STORE or "
            "CORE-4 CORE4_BASELINE"
        )
    selection_rule = (
        RTA4_TASKSET_FIRST_SELECTION_RULE_V3
        if source_core == "CORE-1" else RTA4_SELECTION_RULE_V3
    )
    return {
        "source": _source(source_raw, str(source_core)),
        "utilization_strata": _exact_axis(
            raw["utilization_strata"], "utilization_strata",
            minimum=Fraction(0), maximum=Fraction(1), strict_minimum=True,
        ),
        "candidates_per_method_stratum": candidates,
        "selected_per_method_stratum": selected,
        "methods": _methods(raw["methods"], "methods", RTA4_RECURSIVE_METHODS_V3),
        "workers": _integer_axis(raw["workers"], "workers"),
        "selection_rule": selection_rule,
    }


_NORMALIZERS = {
    "CORE-1": _normalize_core1, "CORE-2": _normalize_core2,
    "CORE-3": _normalize_core3, "CORE-4": _normalize_core4,
    "CORE-5A": _normalize_core5a, "CORE-5B": _normalize_core5b,
}


def normalize_rta4_campaign_v3(raw: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RTA4FormalConfigV3Error("campaign must be a mapping")
    core = raw.get("core")
    if core not in RTA4_CORES_V3:
        raise RTA4FormalConfigV3Error("campaign core is unsupported")
    expected = _COMMON_FIELDS.union(_CORE_FIELDS[str(core)])
    actual = set(raw)
    if actual != expected and actual != expected.difference({"runtime"}):
        raise RTA4FormalConfigV3Error(
            f"campaign field set mismatch; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    campaign_id = raw.get("campaign_id")
    if (type(campaign_id) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", campaign_id)):
        raise RTA4FormalConfigV3Error("campaign_id is not a stable lowercase identifier")
    core_material = _NORMALIZERS[str(core)](raw)
    fixed_semantics: Dict[str, Any] = {
        "task_family": "GENERAL_RANDOM_CONSTRAINED_DEADLINE",
        "priority_policy": "RM",
        "scheduling_model": "GLOBAL_FIXED_PRIORITY",
        "scheduler": "ASAP_BLOCK",
        "energy_analysis": "EXACT_ENERGY",
        "generator": "ASAP_BLOCK_V9_3_FORMAL_UNIFIED_GENERATOR",
    }
    if core in {"CORE-2", "CORE-3"}:
        fixed_semantics.update({
            "source_tasksets": "CORE-1_HASH_BOUND_REUSE",
            "independent_taskset_generation": False,
        })
    if core == "CORE-3":
        fixed_semantics["simulation_scheduler"] = "gpfp_asap_block"
    if core == "CORE-4":
        fixed_semantics["sensitivity_design"] = "ONE_FACTOR_AT_A_TIME"
    if core == "CORE-5B":
        if core_material["source"]["core"] == "CORE-1":
            fixed_semantics.update({
                "source_tasksets": "CORE-1_EXPERIMENT-1_HASH_BOUND_REUSE",
                "independent_taskset_generation": False,
                "selection_unit": "TASKSET_BEFORE_METHOD_CARTESIAN_PRODUCT",
                "selection_rule": RTA4_TASKSET_FIRST_SELECTION_RULE_V3,
                "selection_depends_on_results": False,
            })
        else:
            fixed_semantics.update({
                "source_tasksets": "CORE-4_BASELINE_HASH_BOUND_REUSE",
                "selection_rule": RTA4_SELECTION_RULE_V3,
                "selection_depends_on_results": False,
            })
    scientific = {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "schema_version": RTA4_FORMAL_SCHEMA_VERSION_V3,
        "plan_version": RTA4_FORMAL_PLAN_VERSION_V3,
        "campaign_id": campaign_id,
        "core": core,
        "numeric_contract": {
            "theory_document_sha256": exact_energy.THEORY_DOCUMENT_SHA256,
            "numeric_contract_sha256": RTA4_NUMERIC_CONTRACT_V2_SHA256,
        },
        "fixed_semantics": fixed_semantics,
        **core_material,
    }
    return {
        "normalized_scientific_config": scientific,
        "runtime": _runtime(raw.get("runtime")),
    }


def rta4_formal_config_hash_v3(scientific_config: Mapping[str, Any]) -> str:
    if not isinstance(scientific_config, Mapping):
        raise RTA4FormalConfigV3Error("normalized scientific config must be a mapping")
    if scientific_config.get("profile") != RTA4_FORMAL_PROFILE_V3:
        raise RTA4FormalConfigV3Error("not a V3 normalized scientific config")
    return domain_hash(RTA4_FORMAL_CONFIG_DOMAIN_V3, scientific_config)


def formal_taskset_store_identity_v3(scientific_config: Mapping[str, Any]) -> str:
    scientific_hash = rta4_formal_config_hash_v3(scientific_config)
    material: Dict[str, Any] = {
        "profile": RTA4_FORMAL_PROFILE_V3,
        "store_version": "ASAP_BLOCK_V9_3_RTA4_TASKSET_STORE_V3_PARAMETERIZED",
        "certificate_schema": (
            "ASAP_BLOCK_V9_3_RTA4_W_FREE_TASKSET_CERTIFICATE_V2"
        ),
        "legacy_store_accepted": False,
        "core": scientific_config["core"],
        "scientific_config_sha256": scientific_hash,
    }
    if "source" in scientific_config:
        material["source_taskset_store_identity"] = scientific_config["source"][
            "source_taskset_store_identity"
        ]
    return domain_hash(RTA4_FORMAL_TASKSET_STORE_DOMAIN_V3, material)


def load_rta4_campaign_v3(path: Path | str) -> LoadedCampaignV3:
    campaign_path = Path(path).expanduser().resolve(strict=True)
    payload = campaign_path.read_bytes()
    try:
        raw = yaml.safe_load(payload)
    except Exception as exc:
        raise RTA4FormalConfigV3Error(f"cannot parse campaign YAML: {campaign_path}") from exc
    normalized = normalize_rta4_campaign_v3(raw)
    scientific = normalized["normalized_scientific_config"]
    return LoadedCampaignV3(
        campaign_path=campaign_path,
        raw_campaign_file_sha256=hashlib.sha256(payload).hexdigest(),
        normalized_scientific_config=deepcopy(scientific),
        normalized_scientific_config_sha256=rta4_formal_config_hash_v3(scientific),
        runtime=deepcopy(normalized["runtime"]),
    )


def source_binding_v3(scientific_config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    source = scientific_config.get("source")
    return None if source is None else deepcopy(dict(source))


def validate_source_binding_v3(
    scientific_config: Mapping[str, Any], observed: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = source_binding_v3(scientific_config)
    if expected is None or not isinstance(observed, Mapping) or dict(observed) != expected:
        raise RTA4FormalConfigV3Error("source campaign/taskset identity mismatch")
    return deepcopy(dict(observed))


__all__ = [
    "LoadedCampaignV3", "RTA4_CORE2_METHODS_V3", "RTA4_CORES_V3",
    "RTA4_FORMAL_CONFIG_DOMAIN_V3", "RTA4_FORMAL_PLAN_VERSION_V3",
    "RTA4_FORMAL_PROFILE_V3", "RTA4_FORMAL_SCHEMA_VERSION_V3",
    "RTA4_RECURSIVE_METHODS_V3", "RTA4_SELECTION_RULE_V3",
    "RTA4_TASKSET_FIRST_SELECTION_RULE_V3",
    "RTA4FormalConfigV3Error", "formal_taskset_store_identity_v3",
    "load_rta4_campaign_v3", "normalize_rta4_campaign_v3",
    "rta4_formal_config_hash_v3", "source_binding_v3",
    "validate_source_binding_v3",
]
