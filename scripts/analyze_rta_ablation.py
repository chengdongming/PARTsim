#!/usr/bin/env python3
"""Analyze E3 RTA ablation/refinement endpoint result rows."""

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiment_analysis import (
    diagnostic_output_directory, finalize_diagnostic_outputs,
    validate_attested_analyzer_input,
)


SUMMARY_FILENAME = "rta_ablation_summary.csv"
BY_VARIANT_FILENAME = "rta_ablation_by_variant.csv"
BY_UTILIZATION_FILENAME = "rta_ablation_by_utilization.csv"
BY_VARIANT_UTILIZATION_FILENAME = "rta_ablation_by_variant_utilization.csv"
BY_CONFIG_FILENAME = "rta_ablation_by_config.csv"

RTA_PASS_VARIANT_PLOT = "rta_pass_ratio_by_variant.png"
RTA_PASS_VARIANT_UTILIZATION_PLOT = "rta_pass_ratio_by_variant_utilization.png"
PROOF_CLAIM_VARIANT_PLOT = "proof_claim_pass_ratio_by_variant.png"
RUNTIME_VARIANT_PLOT = "runtime_by_variant.png"
TIMEOUT_VARIANT_PLOT = "timeout_rate_by_variant.png"
BOUND_VARIANT_PLOT = "bound_by_variant.png"

SAFE_CHAIN_STAGES = ("A0", "A1", "A2", "A3")
FORMAL_STAGES = ("A0", "A1", "A2", "A3", "A4")
CANONICAL_BY_NAME = {
    "baseline_safe": ("baseline_safe", "A0", "safe_chain"),
    "a0": ("baseline_safe", "A0", "safe_chain"),
    "carry_in_certified": ("carry_in_certified", "A1", "safe_chain"),
    "a1": ("carry_in_certified", "A1", "safe_chain"),
    "capacity_coupled": ("capacity_coupled", "A2", "safe_chain"),
    "a2": ("capacity_coupled", "A2", "safe_chain"),
    "v20p4_full": ("v20p4_full", "A3", "safe_chain"),
    "a3": ("v20p4_full", "A3", "safe_chain"),
    "v21_local_window_closure": (
        "v21_local_window_closure",
        "A4",
        "local_window_refinement",
    ),
    "v21_experimental": (
        "v21_local_window_closure",
        "A4",
        "local_window_refinement",
    ),
    "a4": ("v21_local_window_closure", "A4", "local_window_refinement"),
}
V21_COUNTER_SUM_COLUMNS = (
    "v21_delta_iterations",
    "v21_g_loc_calls",
    "v21_omega_feasibility_calls",
    "v21_empty_omega_count",
    "v21_no_closure_count",
    "v21_closed_prefix_count",
    "v21_delta_cap_exceeded_count",
    "v21_delta_jump_count",
)
V21_COUNTER_MAX_COLUMNS = ("v21_max_delta_cap", "v21_max_delta_seen")

SUMMARY_COLUMNS = [
    "group_key",
    "group_value",
    "variant_name",
    "variant_canonical",
    "variant_stage",
    "variant_group",
    "variant_label",
    "variant_safety_label",
    "formal_variant",
    "variant_is_default",
    "variant_is_experimental",
    "proof_claim_eligible",
    "diagnostic_only",
    "theory_family",
    "closure_method",
    "certificate_policy",
    "certificate_status",
    "normalized_utilization",
    "config_id",
    "total_rows",
    "attempted_count",
    "completed_count",
    "timeout_count",
    "error_count",
    "unattempted_count",
    "rta_pass_count",
    "rta_fail_count",
    "rta_pass_ratio",
    "rta_pass_yield",
    "proof_claim_eligible_completed_count",
    "proof_claim_eligible_count",
    "proof_claim_allowed_count",
    "proof_claim_succeeded_count",
    "proof_claim_blocked_by_certificate_count",
    "proof_claim_blocked_by_failure_count",
    "proof_claim_pass_count",
    "proof_claim_pass_ratio",
    "formal_variants",
    "formal_variant_row_count",
    "formal_proven_count_by_variant",
    "formal_proven_rate_by_variant",
    "formal_failure_reason_counts_by_variant",
    "formal_certificate_status_counts_by_variant",
    "formal_proof_claim_succeeded_count_by_variant",
    "safe_chain_variants",
    "safe_chain_row_count",
    "safe_chain_proven_count_by_variant",
    "safe_chain_proven_rate_by_variant",
    "A0_to_A1_delta",
    "A1_to_A2_delta",
    "A2_to_A3_delta",
    "A0_to_A3_delta",
    "A3_proven_count",
    "A4_proven_count",
    "both_A3_A4_proven_count",
    "A4_only_proven_count",
    "A3_only_proven_count",
    "both_proven_A4_tighter_count",
    "both_proven_equal_count",
    "both_proven_A4_looser_count",
    "A4_looser_reason_counts",
    "proof_claim_counts_by_assumption_group",
    "A4_delta_iterations_total",
    "A4_g_loc_calls_total",
    "A4_omega_feasibility_calls_total",
    "A4_empty_omega_count_total",
    "A4_no_closure_count_total",
    "A4_closed_prefix_count_total",
    "A4_delta_cap_exceeded_count_total",
    "A4_delta_jump_count_total",
    "A4_max_delta_cap_max",
    "A4_max_delta_seen_max",
    "timeout_rate",
    "error_rate",
    "runtime_sample_count",
    "runtime_mean_sec",
    "runtime_median_sec",
    "runtime_p75_sec",
    "runtime_p90_sec",
    "runtime_p95_sec",
    "runtime_max_sec",
    "bound_sample_count",
    "bound_mean",
    "bound_median",
    "bound_p75",
    "bound_p90",
    "bound_p95",
    "bound_max",
    "profile_runtime_sample_count",
    "profile_runtime_mean_sec",
    "profile_runtime_median_sec",
    "profile_runtime_p95_sec",
    "profile_runtime_max_sec",
    "input_file",
]

REQUIRED_COLUMNS = {
    "variant_name",
    "normalized_utilization",
    "config_id",
    "rta_status",
    "rta_error",
    "result_status",
    "rta_attempted",
    "rta_runtime_sec",
    "rta_timed_out",
    "rta_response_time_bound",
    "rta_response_bound",
    "rta_profile_task_time_sum_sec",
}

TRUE_VALUES = {"1", "true", "yes", "y"}
FORBIDDEN_OUTPUT_FIELDS = {
    "acceptance_ratio",
    "pessimism",
    "observed_max_response_time",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze RTA-only ablation/refinement endpoint results. This does "
            "not compute acceptance ratios, observed pessimism, or scheduler "
            "simulation metrics."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest")
    parser.add_argument(
        "--allow-unattested-diagnostic-input", action="store_true"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "validate input existence and required schema; timeout/error rows "
            "remain valid ablation observations"
        ),
    )
    return parser


def _truthy(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() in TRUE_VALUES


def _nonempty(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() not in {"", "none", "nan", "null"}


def _normalized(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _finite_nonnegative(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    valid = values.map(
        lambda value: (
            pd.notna(value)
            and math.isfinite(value)
            and value >= 0
        )
    ).astype(bool)
    return values.where(valid)


def _error_status(value) -> bool:
    status = _normalized(value)
    return (
        status in {
            "rta_error",
            "error",
            "failed",
            "failure",
            "exception",
            "invalid_json",
            "runner_error",
            "config_error",
            "task_generation_error",
            "version_mismatch",
        }
        or "error" in status
        or "exception" in status
        or status.endswith("_failed")
    )


def _single_value(group: pd.DataFrame, column: str):
    if column not in group.columns or group.empty:
        return "all"
    values = group[column].drop_duplicates().tolist()
    return values[0] if len(values) == 1 else "all"


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else math.nan


def _json_counts(values) -> str:
    counter = Counter()
    for value in values:
        if _nonempty(value):
            counter[str(value).strip()] += 1
    return json.dumps(dict(sorted(counter.items())), sort_keys=True)


def _canonical_info(value):
    normalized = _normalized(value)
    return CANONICAL_BY_NAME.get(normalized, (str(value), "", ""))


def _ensure_column(frame: pd.DataFrame, column: str, default) -> None:
    if column not in frame.columns:
        frame[column] = default


def _normalize_variant_metadata(frame: pd.DataFrame) -> None:
    canonical = frame["variant_name"].map(lambda value: _canonical_info(value)[0])
    stage = frame["variant_name"].map(lambda value: _canonical_info(value)[1])
    group = frame["variant_name"].map(lambda value: _canonical_info(value)[2])
    _ensure_column(frame, "variant_canonical", canonical)
    _ensure_column(frame, "variant_stage", stage)
    _ensure_column(frame, "variant_group", group)
    frame["variant_canonical"] = frame["variant_canonical"].where(
        frame["variant_canonical"].map(_nonempty).astype(bool),
        canonical,
    )
    frame["variant_stage"] = frame["variant_stage"].where(
        frame["variant_stage"].map(_nonempty).astype(bool),
        stage,
    )
    frame["variant_group"] = frame["variant_group"].where(
        frame["variant_group"].map(_nonempty).astype(bool),
        group,
    )
    frame["variant_name"] = frame["variant_canonical"]

    _ensure_column(frame, "variant_label", frame["variant_stage"])
    _ensure_column(frame, "variant_safety_label", "")
    _ensure_column(frame, "formal_variant", True)
    _ensure_column(frame, "variant_is_default", False)
    _ensure_column(frame, "variant_is_experimental", False)
    _ensure_column(frame, "proof_claim_eligible", True)
    _ensure_column(frame, "diagnostic_only", False)
    _ensure_column(frame, "theory_family", "")
    _ensure_column(frame, "closure_method", "")
    _ensure_column(frame, "certificate_policy", "")
    _ensure_column(frame, "certificate_status", "")
    _ensure_column(frame, "proof_claim_allowed", frame["proof_claim_eligible"])
    _ensure_column(frame, "proof_claim_succeeded", False)
    _ensure_column(frame, "failure_reason", "")
    _ensure_column(frame, "fallback_used", False)
    _ensure_column(frame, "fallback_reason", "")
    for column in V21_COUNTER_SUM_COLUMNS + V21_COUNTER_MAX_COLUMNS:
        _ensure_column(frame, column, 0)

    is_a4 = frame["variant_stage"] == "A4"
    is_safe = frame["variant_stage"].isin(SAFE_CHAIN_STAGES)
    frame.loc[is_a4, "variant_safety_label"] = frame.loc[
        is_a4, "variant_safety_label"
    ].map(lambda _value: "safe_under_v21_local_window_assumptions")
    frame.loc[is_safe, "variant_safety_label"] = frame.loc[
        is_safe, "variant_safety_label"
    ].replace("", "safe_under_v20p4_assumptions")
    frame.loc[is_a4, "variant_is_experimental"] = False
    frame.loc[is_a4, "proof_claim_eligible"] = True
    frame.loc[is_a4, "diagnostic_only"] = False
    frame.loc[is_a4, "formal_variant"] = True
    frame.loc[is_safe, "formal_variant"] = True


def _stats(values: pd.Series, prefix: str, suffix: str = "_sec") -> dict:
    finite = _finite_nonnegative(values).dropna()
    names = {
        "sample_count": "{}_sample_count".format(prefix),
        "mean": "{}_mean{}".format(prefix, suffix),
        "median": "{}_median{}".format(prefix, suffix),
        "p75": "{}_p75{}".format(prefix, suffix),
        "p90": "{}_p90{}".format(prefix, suffix),
        "p95": "{}_p95{}".format(prefix, suffix),
        "max": "{}_max{}".format(prefix, suffix),
    }
    return {
        names["sample_count"]: int(len(finite)),
        names["mean"]: float(finite.mean()) if len(finite) else math.nan,
        names["median"]: float(finite.median()) if len(finite) else math.nan,
        names["p75"]: float(finite.quantile(0.75)) if len(finite) else math.nan,
        names["p90"]: float(finite.quantile(0.90)) if len(finite) else math.nan,
        names["p95"]: float(finite.quantile(0.95)) if len(finite) else math.nan,
        names["max"]: float(finite.max()) if len(finite) else math.nan,
    }


def _profile_stats(values: pd.Series) -> dict:
    finite = _finite_nonnegative(values).dropna()
    return {
        "profile_runtime_sample_count": int(len(finite)),
        "profile_runtime_mean_sec": (
            float(finite.mean()) if len(finite) else math.nan
        ),
        "profile_runtime_median_sec": (
            float(finite.median()) if len(finite) else math.nan
        ),
        "profile_runtime_p95_sec": (
            float(finite.quantile(0.95)) if len(finite) else math.nan
        ),
        "profile_runtime_max_sec": (
            float(finite.max()) if len(finite) else math.nan
        ),
    }


def _count_by_variant(group: pd.DataFrame, mask: pd.Series) -> str:
    data = group.loc[mask]
    counts = data.groupby("variant_canonical").size().to_dict()
    return json.dumps(
        {str(key): int(value) for key, value in sorted(counts.items())},
        sort_keys=True,
    )


def _proven_count_by_variant(group: pd.DataFrame, mask: pd.Series) -> str:
    data = group.loc[mask & group["_rta_pass"]]
    counts = data.groupby("variant_canonical").size().to_dict()
    return json.dumps(
        {str(key): int(value) for key, value in sorted(counts.items())},
        sort_keys=True,
    )


def _proven_rate_by_variant(group: pd.DataFrame, mask: pd.Series) -> str:
    rows = group.loc[mask]
    result = {}
    for variant, variant_rows in rows.groupby("variant_canonical"):
        denominator = len(variant_rows)
        result[str(variant)] = (
            float(variant_rows["_rta_pass"].sum()) / denominator
            if denominator
            else math.nan
        )
    return json.dumps(dict(sorted(result.items())), sort_keys=True)


def _counts_by_variant_value(group: pd.DataFrame, mask: pd.Series, column: str) -> str:
    result = {}
    for variant, variant_rows in group.loc[mask].groupby("variant_canonical"):
        result[str(variant)] = json.loads(_json_counts(variant_rows[column]))
    return json.dumps(dict(sorted(result.items())), sort_keys=True)


def _safe_chain_delta(group: pd.DataFrame, later: str, earlier: str):
    by_stage = {
        stage: int(rows["_rta_pass"].sum())
        for stage, rows in group.loc[
            group["variant_stage"].isin(SAFE_CHAIN_STAGES)
        ].groupby("variant_stage")
    }
    if later not in by_stage or earlier not in by_stage:
        return math.nan
    return by_stage[later] - by_stage[earlier]


def _local_window_refinement_metrics(group: pd.DataFrame) -> dict:
    a3 = group.loc[group["variant_stage"] == "A3"].copy()
    a4 = group.loc[group["variant_stage"] == "A4"].copy()
    result = {
        "A3_proven_count": int(a3["_rta_pass"].sum()),
        "A4_proven_count": int(a4["_rta_pass"].sum()),
        "both_A3_A4_proven_count": 0,
        "A4_only_proven_count": 0,
        "A3_only_proven_count": 0,
        "both_proven_A4_tighter_count": 0,
        "both_proven_equal_count": 0,
        "both_proven_A4_looser_count": 0,
        "A4_looser_reason_counts": "{}",
    }
    if a3.empty or a4.empty:
        return result
    keys = [
        key for key in (
            "config_id",
            "taskset_family_id",
            "taskset_id",
            "normalized_utilization",
            "rta_initial_energy",
        )
        if key in group.columns
    ]
    if not keys:
        return result
    merged = a3.merge(a4, on=keys, suffixes=("_a3", "_a4"))
    if merged.empty:
        return result
    a3_pass = merged["_rta_pass_a3"].astype(bool)
    a4_pass = merged["_rta_pass_a4"].astype(bool)
    both = a3_pass & a4_pass
    result["both_A3_A4_proven_count"] = int(both.sum())
    result["A4_only_proven_count"] = int((~a3_pass & a4_pass).sum())
    result["A3_only_proven_count"] = int((a3_pass & ~a4_pass).sum())
    a3_bound = pd.to_numeric(merged["_bound_a3"], errors="coerce")
    a4_bound = pd.to_numeric(merged["_bound_a4"], errors="coerce")
    comparable = both & a3_bound.notna() & a4_bound.notna()
    result["both_proven_A4_tighter_count"] = int(
        (comparable & (a4_bound < a3_bound)).sum()
    )
    result["both_proven_equal_count"] = int(
        (comparable & (a4_bound == a3_bound)).sum()
    )
    looser = comparable & (a4_bound > a3_bound)
    result["both_proven_A4_looser_count"] = int(looser.sum())
    if looser.any():
        result["A4_looser_reason_counts"] = _json_counts(
            merged.loc[looser, "failure_reason_a4"]
            if "failure_reason_a4" in merged.columns
            else pd.Series(["both_proven_A4_bound_larger"] * int(looser.sum()))
        )
    return result


def _proof_claim_assumption_counts(group: pd.DataFrame) -> str:
    result = {}
    for label, rows in group.groupby("variant_safety_label"):
        if not _nonempty(label):
            continue
        eligible = int(rows["_proof_claim_eligible"].sum())
        allowed = int((rows["_proof_claim_eligible"] & rows["_proof_claim_allowed"]).sum())
        succeeded = int(
            (rows["_proof_claim_eligible"] & rows["_proof_claim_succeeded"]).sum()
        )
        result[str(label)] = {
            "eligible": eligible,
            "allowed": allowed,
            "succeeded": succeeded,
        }
    return json.dumps(dict(sorted(result.items())), sort_keys=True)


def _a4_counter_metrics(group: pd.DataFrame) -> dict:
    a4 = group.loc[group["variant_stage"] == "A4"]
    result = {}
    for column in V21_COUNTER_SUM_COLUMNS:
        key = "A4_{}_total".format(column[4:])
        result[key] = int(_finite_nonnegative(a4[column]).sum())
    for column in V21_COUNTER_MAX_COLUMNS:
        key = "A4_{}_max".format(column[4:])
        values = _finite_nonnegative(a4[column]).dropna()
        result[key] = float(values.max()) if len(values) else math.nan
    return result


def load_results(input_path) -> pd.DataFrame:
    path = Path(input_path)
    frame = pd.read_csv(path, keep_default_na=False)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if "rta_schedulable" not in frame.columns and "rta_proven" not in frame.columns:
        missing.append("rta_schedulable or rta_proven")
    if missing:
        raise ValueError(
            "ablation results are missing fields: {}".format(
                ", ".join(missing)
            )
        )
    _normalize_variant_metadata(frame)

    utilization = pd.to_numeric(
        frame["normalized_utilization"], errors="coerce"
    )
    if len(frame) and utilization.isna().any():
        raise ValueError("normalized_utilization contains non-numeric values")
    frame["normalized_utilization"] = utilization

    if len(frame) and frame["variant_name"].map(_nonempty).eq(False).any():
        raise ValueError("variant_name must be non-empty")
    if len(frame) and frame["config_id"].map(_nonempty).eq(False).any():
        raise ValueError("config_id must be non-empty")

    frame["_attempted"] = frame["rta_attempted"].map(_truthy)
    frame["_timeout"] = (
        frame["_attempted"] & frame["rta_timed_out"].map(_truthy)
    )
    error_text = frame["rta_error"].map(_nonempty)
    rta_error_status = frame["rta_status"].map(_error_status)
    result_error_status = frame["result_status"].map(_error_status)
    frame["_error"] = (
        frame["_attempted"]
        & ~frame["_timeout"]
        & (error_text | rta_error_status | result_error_status)
    )
    frame["_completed"] = (
        frame["_attempted"] & ~frame["_timeout"] & ~frame["_error"]
    )

    pass_column = (
        "rta_schedulable"
        if "rta_schedulable" in frame.columns
        else "rta_proven"
    )
    frame["_rta_pass"] = frame[pass_column].map(_truthy)
    frame["_proof_claim_eligible"] = frame["proof_claim_eligible"].map(_truthy)
    frame["_formal_variant"] = frame["formal_variant"].map(_truthy)
    frame["_proof_claim_allowed"] = frame["proof_claim_allowed"].map(_truthy)
    frame["_proof_claim_succeeded"] = frame["proof_claim_succeeded"].map(_truthy)
    frame["_runtime"] = _finite_nonnegative(frame["rta_runtime_sec"])
    frame["_runtime_sample"] = frame["_completed"] & frame["_runtime"].notna()

    response_bound = _finite_nonnegative(frame["rta_response_bound"])
    response_time_bound = _finite_nonnegative(frame["rta_response_time_bound"])
    frame["_bound"] = response_bound.where(response_bound.notna(), response_time_bound)
    frame["_bound_sample"] = frame["_completed"] & frame["_bound"].notna()

    frame["_profile_runtime"] = _finite_nonnegative(
        frame["rta_profile_task_time_sum_sec"]
    )
    frame["_profile_runtime_sample"] = frame["_profile_runtime"].notna()
    for column in V21_COUNTER_SUM_COLUMNS + V21_COUNTER_MAX_COLUMNS:
        frame[column] = _finite_nonnegative(frame[column]).fillna(0)
    return frame


def summarize_group(
    group: pd.DataFrame,
    group_key: str,
    group_value,
    input_path: Path,
    metadata=None,
) -> dict:
    attempted = int(group["_attempted"].sum())
    completed = int(group["_completed"].sum())
    timeout = int(group["_timeout"].sum())
    errors = int(group["_error"].sum())
    passes = int((group["_completed"] & group["_rta_pass"]).sum())
    fails = int(completed - passes)
    eligible_completed_mask = group["_completed"] & group["_proof_claim_eligible"]
    eligible_completed = int(eligible_completed_mask.sum())
    proof_pass = int((eligible_completed_mask & group["_rta_pass"]).sum())
    eligible = int(group["_proof_claim_eligible"].sum())
    allowed = int((group["_proof_claim_eligible"] & group["_proof_claim_allowed"]).sum())
    succeeded = int(
        (group["_proof_claim_eligible"] & group["_proof_claim_succeeded"]).sum()
    )
    blocked_certificate = int(
        (
            group["_proof_claim_eligible"]
            & ~group["_proof_claim_allowed"]
            & group["certificate_status"].astype(str).str.contains(
                "certificate", case=False, na=False
            )
        ).sum()
    )
    blocked_failure = int(
        (
            group["_proof_claim_eligible"]
            & group["_proof_claim_allowed"]
            & ~group["_proof_claim_succeeded"]
        ).sum()
    )
    formal_mask = group["_formal_variant"]
    safe_mask = group["variant_stage"].isin(SAFE_CHAIN_STAGES)

    values = {
        "variant_name": _single_value(group, "variant_name"),
        "variant_canonical": _single_value(group, "variant_canonical"),
        "variant_stage": _single_value(group, "variant_stage"),
        "variant_group": _single_value(group, "variant_group"),
        "variant_label": _single_value(group, "variant_label"),
        "variant_safety_label": _single_value(group, "variant_safety_label"),
        "formal_variant": _single_value(group, "formal_variant"),
        "variant_is_default": _single_value(group, "variant_is_default"),
        "variant_is_experimental": _single_value(group, "variant_is_experimental"),
        "proof_claim_eligible": _single_value(group, "proof_claim_eligible"),
        "diagnostic_only": _single_value(group, "diagnostic_only"),
        "theory_family": _single_value(group, "theory_family"),
        "closure_method": _single_value(group, "closure_method"),
        "certificate_policy": _single_value(group, "certificate_policy"),
        "certificate_status": _single_value(group, "certificate_status"),
        "normalized_utilization": _single_value(group, "normalized_utilization"),
        "config_id": _single_value(group, "config_id"),
    }
    if metadata:
        values.update(metadata)
    row = {
        "group_key": group_key,
        "group_value": group_value,
        **values,
        "total_rows": int(len(group)),
        "attempted_count": attempted,
        "completed_count": completed,
        "timeout_count": timeout,
        "error_count": errors,
        "unattempted_count": int(len(group) - attempted),
        "rta_pass_count": passes,
        "rta_fail_count": fails,
        "rta_pass_ratio": _ratio(passes, completed),
        "rta_pass_yield": _ratio(passes, attempted),
        "proof_claim_eligible_completed_count": eligible_completed,
        "proof_claim_eligible_count": eligible,
        "proof_claim_allowed_count": allowed,
        "proof_claim_succeeded_count": succeeded,
        "proof_claim_blocked_by_certificate_count": blocked_certificate,
        "proof_claim_blocked_by_failure_count": blocked_failure,
        "proof_claim_pass_count": proof_pass,
        "proof_claim_pass_ratio": _ratio(proof_pass, eligible_completed),
        "formal_variants": ",".join(FORMAL_STAGES),
        "formal_variant_row_count": int(formal_mask.sum()),
        "formal_proven_count_by_variant": _proven_count_by_variant(
            group, formal_mask
        ),
        "formal_proven_rate_by_variant": _proven_rate_by_variant(
            group, formal_mask
        ),
        "formal_failure_reason_counts_by_variant": _counts_by_variant_value(
            group, formal_mask, "failure_reason"
        ),
        "formal_certificate_status_counts_by_variant": _counts_by_variant_value(
            group, formal_mask, "certificate_status"
        ),
        "formal_proof_claim_succeeded_count_by_variant": _count_by_variant(
            group, formal_mask & group["_proof_claim_succeeded"]
        ),
        "safe_chain_variants": ",".join(SAFE_CHAIN_STAGES),
        "safe_chain_row_count": int(safe_mask.sum()),
        "safe_chain_proven_count_by_variant": _proven_count_by_variant(
            group, safe_mask
        ),
        "safe_chain_proven_rate_by_variant": _proven_rate_by_variant(
            group, safe_mask
        ),
        "A0_to_A1_delta": _safe_chain_delta(group, "A1", "A0"),
        "A1_to_A2_delta": _safe_chain_delta(group, "A2", "A1"),
        "A2_to_A3_delta": _safe_chain_delta(group, "A3", "A2"),
        "A0_to_A3_delta": _safe_chain_delta(group, "A3", "A0"),
        **_local_window_refinement_metrics(group),
        "proof_claim_counts_by_assumption_group": _proof_claim_assumption_counts(group),
        **_a4_counter_metrics(group),
        "timeout_rate": _ratio(timeout, attempted),
        "error_rate": _ratio(errors, attempted),
        "input_file": str(input_path),
    }
    row.update(_stats(group.loc[group["_runtime_sample"], "_runtime"], "runtime"))
    row.update(_stats(group.loc[group["_bound_sample"], "_bound"], "bound", suffix=""))
    row.update(_profile_stats(
        group.loc[group["_profile_runtime_sample"], "_profile_runtime"]
    ))
    return {column: row.get(column, math.nan) for column in SUMMARY_COLUMNS}


def build_overall(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    row = summarize_group(
        frame,
        "overall",
        "all",
        input_path,
        {
            "variant_name": "all",
            "variant_canonical": "all",
            "variant_stage": "all",
            "variant_group": "all",
            "variant_label": "all",
            "variant_safety_label": "all",
            "formal_variant": "all",
            "variant_is_default": "all",
            "variant_is_experimental": "all",
            "proof_claim_eligible": "all",
            "diagnostic_only": "all",
            "theory_family": "all",
            "closure_method": "all",
            "certificate_policy": "all",
            "certificate_status": "all",
            "normalized_utilization": "all",
            "config_id": "all",
        },
    )
    return pd.DataFrame([row], columns=SUMMARY_COLUMNS)


def build_by_variant(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "variant_name",
            variant,
            input_path,
            {
                "variant_name": variant,
                "variant_canonical": variant,
                "normalized_utilization": "all",
                "config_id": "all",
            },
        )
        for variant, group in frame.groupby("variant_name", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_utilization(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "normalized_utilization",
            utilization,
            input_path,
            {
                "variant_name": "all",
                "variant_canonical": "all",
                "variant_stage": "all",
                "variant_group": "all",
                "variant_label": "all",
                "variant_safety_label": "all",
                "formal_variant": "all",
                "variant_is_default": "all",
                "variant_is_experimental": "all",
                "proof_claim_eligible": "all",
                "diagnostic_only": "all",
                "theory_family": "all",
                "closure_method": "all",
                "certificate_policy": "all",
                "certificate_status": "all",
                "normalized_utilization": utilization,
                "config_id": "all",
            },
        )
        for utilization, group in frame.groupby("normalized_utilization", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_variant_utilization(
    frame: pd.DataFrame, input_path: Path
) -> pd.DataFrame:
    rows = []
    for (variant, utilization), group in frame.groupby(
        ["variant_name", "normalized_utilization"], sort=True
    ):
        rows.append(summarize_group(
            group,
            "variant_utilization",
            "{}@{}".format(variant, utilization),
            input_path,
            {
                "variant_name": variant,
                "normalized_utilization": utilization,
                "config_id": "all",
            },
        ))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_by_config(frame: pd.DataFrame, input_path: Path) -> pd.DataFrame:
    rows = [
        summarize_group(
            group,
            "config_id",
            config_id,
            input_path,
            {"config_id": config_id},
        )
        for config_id, group in frame.groupby("config_id", sort=True)
    ]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _no_data(axis, message: str) -> None:
    axis.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        transform=axis.transAxes,
    )


def _plot_bar(
    frame: pd.DataFrame,
    column: str,
    output: Path,
    y_label: str,
    title: str,
    no_data: str,
) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    labels = frame.get("variant_name", pd.Series(dtype=str)).astype(str)
    values = pd.to_numeric(frame.get(column), errors="coerce")
    mask = labels.map(_nonempty) & values.notna()
    if mask.any():
        positions = range(int(mask.sum()))
        axis.bar(list(positions), values[mask])
        axis.set_xticks(list(positions))
        axis.set_xticklabels(labels[mask], rotation=25, ha="right")
    else:
        _no_data(axis, no_data)
    axis.set_ylabel(y_label)
    axis.set_title(title)
    if "ratio" in column or "rate" in column:
        axis.set_ylim(0, 1.02)
    axis.grid(True, axis="y", linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_variant_utilization(by_variant_utilization: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    plotted = False
    for variant, group in by_variant_utilization.groupby("variant_name", sort=True):
        x = pd.to_numeric(group.get("normalized_utilization"), errors="coerce")
        y = pd.to_numeric(group.get("rta_pass_ratio"), errors="coerce")
        mask = x.notna() & y.notna()
        if mask.any():
            order = x[mask].sort_values().index
            axis.plot(
                x.loc[order],
                y.loc[order],
                marker="o",
                label=str(variant),
            )
            plotted = True
    if plotted:
        axis.legend()
    else:
        _no_data(axis, "No endpoint pass-ratio data")
    axis.set_xlabel("Normalized utilization")
    axis.set_ylabel("Endpoint RTA pass ratio")
    axis.set_ylim(0, 1.02)
    axis.set_title("Endpoint RTA pass ratio by variant and utilization")
    axis.grid(True, linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_runtime(by_variant: pd.DataFrame, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4.5))
    labels = by_variant.get("variant_name", pd.Series(dtype=str)).astype(str)
    median = pd.to_numeric(by_variant.get("runtime_median_sec"), errors="coerce")
    p95 = pd.to_numeric(by_variant.get("runtime_p95_sec"), errors="coerce")
    mask = labels.map(_nonempty) & (median.notna() | p95.notna())
    if mask.any():
        positions = list(range(int(mask.sum())))
        selected_labels = labels[mask]
        selected_median = median[mask]
        selected_p95 = p95[mask]
        width = 0.35
        axis.bar(
            [position - width / 2 for position in positions],
            selected_median,
            width=width,
            label="median",
        )
        axis.bar(
            [position + width / 2 for position in positions],
            selected_p95,
            width=width,
            label="p95",
        )
        axis.set_xticks(positions)
        axis.set_xticklabels(selected_labels, rotation=25, ha="right")
        axis.legend()
    else:
        _no_data(axis, "No canonical runtime data")
    axis.set_ylabel("RTA subprocess wall-clock runtime (sec)")
    axis.set_title("RTA wall-clock runtime by variant")
    axis.grid(True, axis="y", linestyle="--", alpha=0.35)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(input_path, output_dir, manifest_path=None):
    input_path = Path(input_path)
    if manifest_path is not None and not Path(manifest_path).is_file():
        raise FileNotFoundError("manifest does not exist: {}".format(manifest_path))
    frame = load_results(input_path)
    output = Path(output_dir)
    plots = output / "plots"
    output.mkdir(parents=True, exist_ok=True)
    plots.mkdir(parents=True, exist_ok=True)

    overall = build_overall(frame, input_path)
    by_variant = build_by_variant(frame, input_path)
    by_utilization = build_by_utilization(frame, input_path)
    by_variant_utilization = build_by_variant_utilization(frame, input_path)
    by_config = build_by_config(frame, input_path)

    overall.to_csv(output / SUMMARY_FILENAME, index=False)
    by_variant.to_csv(output / BY_VARIANT_FILENAME, index=False)
    by_utilization.to_csv(output / BY_UTILIZATION_FILENAME, index=False)
    by_variant_utilization.to_csv(
        output / BY_VARIANT_UTILIZATION_FILENAME, index=False
    )
    by_config.to_csv(output / BY_CONFIG_FILENAME, index=False)

    _plot_bar(
        by_variant,
        "rta_pass_ratio",
        plots / RTA_PASS_VARIANT_PLOT,
        "Endpoint RTA pass ratio",
        "Endpoint RTA pass ratio by variant",
        "No completed endpoint rows",
    )
    _plot_variant_utilization(
        by_variant_utilization,
        plots / RTA_PASS_VARIANT_UTILIZATION_PLOT,
    )
    proof_rows = by_variant.loc[
        by_variant["proof_claim_eligible"].map(_truthy)
    ].copy()
    _plot_bar(
        proof_rows,
        "proof_claim_pass_ratio",
        plots / PROOF_CLAIM_VARIANT_PLOT,
        "Proof-claim pass ratio",
        "Proof-claim pass ratio for eligible variants only",
        "No proof-claim eligible completed rows",
    )
    _plot_runtime(by_variant, plots / RUNTIME_VARIANT_PLOT)
    _plot_bar(
        by_variant,
        "timeout_rate",
        plots / TIMEOUT_VARIANT_PLOT,
        "RTA timeout rate",
        "RTA timeout rate by variant",
        "No attempted rows",
    )
    _plot_bar(
        by_variant,
        "bound_median",
        plots / BOUND_VARIANT_PLOT,
        "Median RTA response bound",
        "RTA response bound by variant",
        "No completed bound samples",
    )
    return overall, by_variant, by_utilization, by_variant_utilization, by_config


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir = Path(args.output_dir)
        if args.allow_unattested_diagnostic_input:
            output_dir = diagnostic_output_directory(output_dir)
        else:
            validate_attested_analyzer_input(args.input)
        analyze(args.input, output_dir, args.manifest)
        if args.allow_unattested_diagnostic_input:
            finalize_diagnostic_outputs(output_dir)
    except (OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
