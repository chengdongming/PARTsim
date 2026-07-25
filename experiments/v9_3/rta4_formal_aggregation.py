"""Validated, paired aggregation for figures 1--5 and tables 1--3."""

from __future__ import annotations

from collections import defaultdict
import csv
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import random
import statistics
from typing import Any, Dict, Mapping, Sequence

from .result_writer import atomic_write_json, write_csv
from .rta4_formal_config import canonical_json
from .rta4_formal_environment import load_strict_json
from .rta4_formal_validation import (
    ValidatedFormalClosure, refresh_validated_closure,
    validate_formal_run_closure,
)
from .rta4_formal_writer import FORMAL_AUTHORIZATION_EVIDENCE


RTA4_AGGREGATE_VERSION = "ASAP_BLOCK_V9_3_RTA4_AGGREGATE_V1"
RTA4_AGGREGATE_MANIFEST = "rta4_aggregate_manifest.json"
RTA4_BOOTSTRAP_CONTRACT = "TASKSET_SKELETON_CLUSTER_BOOTSTRAP_V1"
RTA4_BOOTSTRAP_REPLICATES = 10_000
RTA4_BOOTSTRAP_SEED = 930612

FIGURE_1_COLUMNS = (
    "row_type", "method", "normalized_utilization", "exact_e0", "relation",
    "sample_count", "denominator", "estimate", "ci_lower", "ci_upper",
    "median", "p95", "iqr_lower", "iqr_upper",
)
FIGURE_2_COLUMNS = (
    "row_type", "relation", "method", "normalized_utilization", "exact_e0",
    "sample_count", "denominator", "estimate", "median", "iqr_lower", "iqr_upper",
)
FIGURE_3_COLUMNS = (
    "row_type", "release_mode", "applicability_track", "battery_capacity",
    "normalized_utilization", "method", "exact_e0", "classification",
    "sample_count", "denominator", "estimate", "p95", "maximum",
)
FIGURE_4_COLUMNS = (
    "axis", "axis_value", "method", "normalized_utilization_stratum",
    "sample_count", "denominator", "certification_rate", "ci_lower", "ci_upper",
)
FIGURE_5_COLUMNS = (
    "row_type", "axis", "axis_value", "method", "worker_count",
    "sample_count", "runtime_median", "runtime_p95", "speedup",
    "parallel_efficiency", "mathematical_mismatch_count",
)
TABLE_1_COLUMNS = ("parameter", "exact_value", "source", "status")
TABLE_2_COLUMNS = (
    "method", "requested", "certified", "no_candidate", "timeout",
    "numeric_error", "internal_error", "certification_rate", "runtime_median",
    "runtime_p95", "dominance_violation_count",
)
TABLE_3_COLUMNS = (
    "applicability_track", "release_mode", "classification", "count",
    "candidate_exceedance_count", "e0_not_satisfied_count",
    "theorem_inapplicable_count",
)

AGGREGATE_TABLES = {
    "figure_1_rta_comparison.csv": FIGURE_1_COLUMNS,
    "figure_2_ablation_mechanisms.csv": FIGURE_2_COLUMNS,
    "figure_3_rta_simulation_audit.csv": FIGURE_3_COLUMNS,
    "figure_4_sensitivity.csv": FIGURE_4_COLUMNS,
    "figure_5_scalability.csv": FIGURE_5_COLUMNS,
    "table_1_parameters.csv": TABLE_1_COLUMNS,
    "table_2_rta_summary.csv": TABLE_2_COLUMNS,
    "table_3_simulation_audit.csv": TABLE_3_COLUMNS,
}

CORE_AGGREGATE_TABLES = {
    "CORE-1": ("figure_1_rta_comparison.csv", "table_1_parameters.csv", "table_2_rta_summary.csv"),
    "CORE-2": ("figure_2_ablation_mechanisms.csv", "table_1_parameters.csv"),
    "CORE-3": ("figure_3_rta_simulation_audit.csv", "table_1_parameters.csv", "table_3_simulation_audit.csv"),
    "CORE-4": ("figure_4_sensitivity.csv", "table_1_parameters.csv"),
    "CORE-5A": ("figure_5_scalability.csv", "table_1_parameters.csv"),
    "CORE-5B": ("figure_5_scalability.csv", "table_1_parameters.csv"),
}


class RTA4FormalAggregationError(RuntimeError):
    """Raised if closure validation or a statistical domain is incomplete."""


def _truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true"}


def _float(value: Any) -> float | None:
    if value in {None, "", "NA"}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RTA4FormalAggregationError(f"invalid numeric aggregate input: {value!r}") from exc
    if not math.isfinite(result):
        raise RTA4FormalAggregationError("aggregate input contains NaN/Inf")
    return result


def _quantile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _number(value: float | None) -> str:
    if value is None:
        return "NA"
    return format(value, ".17g")


def cluster_bootstrap_binary(
    rows: Sequence[Mapping[str, Any]], *, cluster_key: str, value_key: str,
    replicates: int = RTA4_BOOTSTRAP_REPLICATES,
    seed: int = RTA4_BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """Deterministic taskset-skeleton cluster bootstrap 95% interval."""

    if type(replicates) is not int or replicates < 1:
        raise RTA4FormalAggregationError("bootstrap replicates must be positive")
    clusters: Dict[str, list[float]] = defaultdict(list)
    for row in rows:
        identity = str(row.get(cluster_key, ""))
        if not identity:
            raise RTA4FormalAggregationError("bootstrap row lacks cluster identity")
        clusters[identity].append(1.0 if _truth(row.get(value_key)) else 0.0)
    cluster_values = [math.fsum(values) / len(values) for _, values in sorted(clusters.items())]
    if not cluster_values:
        return None, None
    rng = random.Random(seed)
    size = len(cluster_values)
    estimates = [
        math.fsum(cluster_values[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(replicates)
    ]
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def _figure1(
    closure: ValidatedFormalClosure, replicates: int,
) -> list[Dict[str, Any]]:
    results = closure.table("formal_rta_taskset_results.csv")
    tasks = closure.table("formal_rta_task_results.csv")
    output: list[Dict[str, Any]] = []
    groups: Dict[tuple[str, str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in results:
        if row["method"] in {"CW_THETA_CW", "LOC_THETA_LOC", "PH_THETA_PH", "SEQ_THETA_SEQ"}:
            groups[(row["method"], row["normalized_utilization"], row["exact_e0"])].append(row)
    for (method, utilization, e0), rows in sorted(groups.items()):
        certified = sum(_truth(row["taskset_proven"]) for row in rows)
        lower, upper = cluster_bootstrap_binary(
            rows, cluster_key="taskset_skeleton_id", value_key="taskset_proven",
            replicates=replicates,
            seed=RTA4_BOOTSTRAP_SEED + int(hashlib.sha256("|".join((method, utilization, e0)).encode()).hexdigest()[:8], 16),
        )
        runtimes = [
            value for value in (_float(row["runtime_wall_seconds"]) for row in rows)
            if value is not None and value > 0
        ]
        output.append({
            "row_type": "TASKSET_CERTIFICATION_RATE", "method": method,
            "normalized_utilization": utilization, "exact_e0": e0,
            "relation": "NA", "sample_count": len(rows), "denominator": len(rows),
            "estimate": _number(certified / len(rows)),
            "ci_lower": _number(lower), "ci_upper": _number(upper),
            "median": _number(_quantile(runtimes, .5)),
            "p95": _number(_quantile(runtimes, .95)),
            "iqr_lower": "NA", "iqr_upper": "NA",
        })
    tasks_by_analysis: Dict[str, Dict[str, Mapping[str, str]]] = defaultdict(dict)
    for row in tasks:
        tasks_by_analysis[row["analysis_id"]][row["task_id"]] = row
    relations = (
        ("CW_TO_LOC", "CW_THETA_CW", "LOC_THETA_LOC"),
        ("LOC_TO_PH", "LOC_THETA_LOC", "PH_THETA_PH"),
        ("PH_TO_SEQ", "PH_THETA_PH", "SEQ_THETA_SEQ"),
        ("CW_TO_SEQ", "CW_THETA_CW", "SEQ_THETA_SEQ"),
    )
    result_index = {}
    bundle_identity = (
        closure.metadata["core"], closure.metadata["plan_sha256"],
        closure.closure_sha256,
    )
    for row in results:
        domain = bundle_identity + (
            row["taskset_skeleton_id"], row["taskset_id"], row["exact_e0"],
            row["service_identity"], row["power_vector_hash"], row["deadline_variant"],
            row["scenario"], row["axis"], row["axis_value"], row["method"],
        )
        if domain in result_index:
            raise RTA4FormalAggregationError("duplicate complete pairing domain")
        result_index[domain] = row
    for relation, weak, strong in relations:
        reductions: Dict[tuple[str, str], list[float]] = defaultdict(list)
        for key, left_result in sorted(result_index.items()):
            if key[-1] != weak:
                continue
            right_result = result_index.get((*key[:-1], strong))
            if right_result is None:
                continue
            left_tasks = tasks_by_analysis[left_result["analysis_id"]]
            right_tasks = tasks_by_analysis[right_result["analysis_id"]]
            for task_id in sorted(set(left_tasks) & set(right_tasks)):
                left_candidate = _float(left_tasks[task_id]["candidate_response_time"])
                right_candidate = _float(right_tasks[task_id]["candidate_response_time"])
                if left_candidate is None or right_candidate is None or left_candidate <= 0:
                    continue
                reductions[(left_result["normalized_utilization"], left_result["exact_e0"])].append(
                    (left_candidate - right_candidate) / left_candidate
                )
        for (utilization, e0), values in sorted(reductions.items()):
            output.append({
                "row_type": "PAIRED_RESPONSE_REDUCTION", "method": "NA",
                "normalized_utilization": utilization, "exact_e0": e0,
                "relation": relation, "sample_count": len(values),
                "denominator": len(values), "estimate": _number(statistics.median(values)),
                "ci_lower": "NA", "ci_upper": "NA",
                "median": _number(statistics.median(values)), "p95": "NA",
                "iqr_lower": _number(_quantile(values, .25)),
                "iqr_upper": _number(_quantile(values, .75)),
            })
    return output


def _figure2(
    closure: ValidatedFormalClosure,
    source: ValidatedFormalClosure,
) -> list[Dict[str, Any]]:
    mechanisms = closure.table("formal_rta_mechanisms.csv")
    output: list[Dict[str, Any]] = []
    bundle_identity = (
        closure.metadata["core"], closure.metadata["plan_sha256"],
        closure.closure_sha256, source.metadata["plan_sha256"],
        source.closure_sha256,
    )
    target_methods = {
        "CW_D", "LOC_D", "PH_D", "SEQ_D", "CW_THETA_CW", "SEQ_THETA_SEQ",
    }
    source_methods = {"LOC_THETA_LOC", "PH_THETA_PH"}

    def provenance_index(
        rows: Sequence[Mapping[str, str]], *, allowed: set[str], origin: str,
    ) -> Dict[tuple[str, ...], Mapping[str, str]]:
        index: Dict[tuple[str, ...], Mapping[str, str]] = {}
        for row in rows:
            if row["method"] not in allowed:
                continue
            domain = bundle_identity + (
                row["taskset_skeleton_id"], row["taskset_id"], row["exact_e0"],
                row["service_identity"], row["power_vector_hash"],
                row["deadline_variant"], row["scenario"], row["axis"],
                row["axis_value"], row["method"],
            )
            if domain in index:
                raise RTA4FormalAggregationError(
                    f"duplicate {origin} Figure 2 pairing domain"
                )
            index[domain] = row
        return index

    target_index = provenance_index(
        closure.table("formal_rta_taskset_results.csv"),
        allowed=target_methods, origin="TARGET_CORE2",
    )
    source_index = provenance_index(
        source.table("formal_rta_taskset_results.csv"),
        allowed=source_methods, origin="SOURCE_CORE1",
    )
    relations = (
        ("LOC_D_MINUS_CW_D", target_index, "CW_D", target_index, "LOC_D"),
        ("PH_D_MINUS_LOC_D", target_index, "LOC_D", target_index, "PH_D"),
        ("SEQ_D_MINUS_PH_D", target_index, "PH_D", target_index, "SEQ_D"),
        ("LOC_THETA_MINUS_CW_THETA", target_index, "CW_THETA_CW", source_index, "LOC_THETA_LOC"),
        ("PH_THETA_MINUS_LOC_THETA", source_index, "LOC_THETA_LOC", source_index, "PH_THETA_PH"),
        ("SEQ_THETA_MINUS_PH_THETA", source_index, "PH_THETA_PH", target_index, "SEQ_THETA_SEQ"),
    )
    for relation, left_index, weak, right_index, strong in relations:
        groups: Dict[tuple[str, str], list[float]] = defaultdict(list)
        for key in sorted(left_index):
            if key[-1] != weak:
                continue
            left = left_index[key]
            right = right_index.get((*key[:-1], strong))
            if right is None:
                continue
            groups[(left["normalized_utilization"], left["exact_e0"])].append(
                float(_truth(right["taskset_proven"])) - float(_truth(left["taskset_proven"]))
            )
        for (utilization, e0), values in sorted(groups.items()):
            output.append({
                "row_type": "CERTIFICATION_GAIN", "relation": relation,
                "method": strong, "normalized_utilization": utilization,
                "exact_e0": e0, "sample_count": len(values),
                "denominator": len(values), "estimate": _number(math.fsum(values) / len(values)),
                "median": _number(statistics.median(values)),
                "iqr_lower": _number(_quantile(values, .25)),
                "iqr_upper": _number(_quantile(values, .75)),
            })
    event_fields = (
        "strict_ph_lt_loc_checkpoints", "ph_no_common_h_but_seq_exists",
        "sequence_kind",
    )
    for field in event_fields:
        applicable = [row for row in mechanisms if row[field] not in {"", "NA"}]
        event_count = sum(
            (_truth(row[field]) if field != "sequence_kind" else row[field] == "NONCONSTANT")
            for row in applicable
        )
        output.append({
            "row_type": "MECHANISM_RATE", "relation": field, "method": "NA",
            "normalized_utilization": "ALL", "exact_e0": "ALL",
            "sample_count": len(applicable), "denominator": len(applicable),
            "estimate": _number(event_count / len(applicable)) if applicable else "NA",
            "median": "NA", "iqr_lower": "NA", "iqr_upper": "NA",
        })
    return output


def _figure3(
    closure: ValidatedFormalClosure,
    source: ValidatedFormalClosure,
) -> list[Dict[str, Any]]:
    simulations = {row["simulation_id"]: row for row in closure.table("formal_simulation_runs.csv")}
    applicability = closure.table("formal_applicability.csv")
    results = {
        row["analysis_id"]: row
        for row in source.table("formal_rta_taskset_results.csv")
    }
    output: list[Dict[str, Any]] = []
    groups: Dict[tuple[str, ...], list[Mapping[str, str]]] = defaultdict(list)
    for row in applicability:
        simulation = simulations[row["simulation_id"]]
        result = results.get(row["analysis_id"], {})
        key = (
            simulation["release_mode"], simulation["applicability_track"],
            simulation["battery_capacity"] or "NA",
            result.get("normalized_utilization", "NA"), row["method"],
            row["exact_e0"], row["comparison_status"],
        )
        groups[key].append(row)
    for key, rows in sorted(groups.items()):
        release, track, battery, utilization, method, e0, classification = key
        ratios = []
        for row in rows:
            candidate = _float(row["candidate_response_time"])
            observed = _float(row["observed_response_time"])
            if candidate is not None and observed is not None and candidate > 0:
                ratios.append(observed / candidate)
        output.append({
            "row_type": "RTA_SIMULATION_CLASS", "release_mode": release,
            "applicability_track": track, "battery_capacity": battery,
            "normalized_utilization": utilization, "method": method,
            "exact_e0": e0, "classification": classification,
            "sample_count": len(rows), "denominator": len(rows), "estimate": _number(1.0),
            "p95": _number(_quantile(ratios, .95)),
            "maximum": _number(max(ratios) if ratios else None),
        })
    return output


def _figure4(closure: ValidatedFormalClosure, replicates: int) -> list[Dict[str, Any]]:
    groups: Dict[tuple[str, str, str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in closure.table("formal_rta_taskset_results.csv"):
        if row["axis"] in {"e0", "service_scale", "power_scale", "deadline_slack_fraction"}:
            groups[(row["axis"], row["axis_value"], row["method"], row["normalized_utilization"])].append(row)
    output = []
    for key, rows in sorted(groups.items()):
        certified = sum(_truth(row["taskset_proven"]) for row in rows)
        lower, upper = cluster_bootstrap_binary(
            rows, cluster_key="taskset_skeleton_id", value_key="taskset_proven",
            replicates=replicates,
        )
        output.append({
            "axis": key[0], "axis_value": key[1], "method": key[2],
            "normalized_utilization_stratum": key[3], "sample_count": len(rows),
            "denominator": len(rows), "certification_rate": _number(certified / len(rows)),
            "ci_lower": _number(lower), "ci_upper": _number(upper),
        })
    return output


def _figure5(closure: ValidatedFormalClosure) -> list[Dict[str, Any]]:
    output = []
    groups: Dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in closure.table("formal_rta_taskset_results.csv"):
        if row["axis"] in {"task_count", "processor_count", "integer_time_scale"}:
            runtime = _float(row["runtime_wall_seconds"])
            if runtime is not None and runtime > 0:
                groups[(row["axis"], row["axis_value"], row["method"])].append(runtime)
    for (axis, value, method), runtimes in sorted(groups.items()):
        output.append({
            "row_type": "ALGORITHMIC_SCALING", "axis": axis, "axis_value": value,
            "method": method, "worker_count": "1", "sample_count": len(runtimes),
            "runtime_median": _number(_quantile(runtimes, .5)),
            "runtime_p95": _number(_quantile(runtimes, .95)), "speedup": "NA",
            "parallel_efficiency": "NA", "mathematical_mismatch_count": 0,
        })
    worker_groups: Dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in closure.table("formal_worker_consistency.csv"):
        worker_groups[(row["compared_worker_count"], row["check_status"])].append(row)
    for (workers, status), rows in sorted(worker_groups.items()):
        mismatches = sum(row["math_hash_match"].lower() != "true" for row in rows)
        output.append({
            "row_type": "WORKER_CONSISTENCY", "axis": "worker_count",
            "axis_value": workers, "method": "ALL", "worker_count": workers,
            "sample_count": len(rows), "runtime_median": "NA", "runtime_p95": "NA",
            "speedup": "NA", "parallel_efficiency": "NA",
            "mathematical_mismatch_count": mismatches,
        })
    return output


def _table1(closure: ValidatedFormalClosure) -> list[Dict[str, Any]]:
    skeletons = closure.table("formal_taskset_skeletons.csv")
    tasks = closure.table("formal_tasks.csv")
    simulations = closure.table("formal_simulation_runs.csv")
    values = {
        "taskset_skeleton_count": len(skeletons),
        "task_count_rows": len(tasks),
        "release_horizon": sorted({row["release_horizon"] for row in simulations}) or ["NA"],
        "observation_horizon_contract": "H_g + D_max",
        "bootstrap_cluster": "taskset_skeleton_id",
        "bootstrap_replicates": RTA4_BOOTSTRAP_REPLICATES,
    }
    return [
        {"parameter": key, "exact_value": canonical_json(value), "source": "VALIDATED_CLOSURE", "status": "RECORDED"}
        for key, value in values.items()
    ]


def _table2(closure: ValidatedFormalClosure) -> list[Dict[str, Any]]:
    groups: Dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in closure.table("formal_rta_taskset_results.csv"):
        groups[row["method"]].append(row)
    dominance = closure.table("formal_dominance_checks.csv")
    output = []
    for method, rows in sorted(groups.items()):
        runtimes = [value for value in (_float(row["runtime_wall_seconds"]) for row in rows) if value is not None and value > 0]
        statuses = [row["solver_status"] for row in rows]
        output.append({
            "method": method, "requested": len(rows),
            "certified": sum(_truth(row["taskset_proven"]) for row in rows),
            "no_candidate": sum(status == "NO_CANDIDATE" for status in statuses),
            "timeout": sum(_truth(row["timeout"]) for row in rows),
            "numeric_error": sum("NUMERIC" in status for status in statuses),
            "internal_error": sum("INTERNAL" in status for status in statuses),
            "certification_rate": _number(sum(_truth(row["taskset_proven"]) for row in rows) / len(rows)),
            "runtime_median": _number(_quantile(runtimes, .5)),
            "runtime_p95": _number(_quantile(runtimes, .95)),
            "dominance_violation_count": sum(
                row["failure_severity"] == "P0" and method in {row["left_method"], row["right_method"]}
                for row in dominance
            ),
        })
    return output


def _table3(closure: ValidatedFormalClosure) -> list[Dict[str, Any]]:
    simulations = {row["simulation_id"]: row for row in closure.table("formal_simulation_runs.csv")}
    groups: Dict[tuple[str, str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in closure.table("formal_applicability.csv"):
        simulation = simulations[row["simulation_id"]]
        groups[(simulation["applicability_track"], simulation["release_mode"], row["comparison_status"])].append(row)
    return [{
        "applicability_track": key[0], "release_mode": key[1],
        "classification": key[2], "count": len(rows),
        "candidate_exceedance_count": sum(_truth(row["soundness_counterexample"]) for row in rows),
        "e0_not_satisfied_count": sum(row["e0_condition_status"] == "E0_CONDITION_NOT_SATISFIED" for row in rows),
        "theorem_inapplicable_count": sum(not _truth(row["theorem_comparison_eligible"]) for row in rows),
    } for key, rows in sorted(groups.items())]


def aggregate_formal_run(
    root: Path | str, output_root: Path | str, *,
    bootstrap_replicates: int = RTA4_BOOTSTRAP_REPLICATES,
    source_closures: Mapping[str, Path | str | Any] | None = None,
    require_authorized_formal: bool = False,
    allow_test_authorization: bool = False,
) -> Mapping[str, Any]:
    sources = source_closures or {}
    closure = validate_formal_run_closure(
        root, require_complete=True,
        require_authorized_formal=require_authorized_formal,
        source_closures=sources,
        allow_test_authorization=allow_test_authorization,
    )
    if type(bootstrap_replicates) is not int or bootstrap_replicates < 1:
        raise RTA4FormalAggregationError("bootstrap replicates must be positive")
    if (
        closure.metadata.get("execution_class") == "FORMAL_AUTHORIZED"
        and bootstrap_replicates != RTA4_BOOTSTRAP_REPLICATES
    ):
        raise RTA4FormalAggregationError("formal profile fixes bootstrap at 10,000 replicates")
    output_root = Path(output_root)
    if output_root.exists() and any(output_root.iterdir()):
        raise RTA4FormalAggregationError("aggregate output root must be empty")
    output_root.mkdir(parents=True, exist_ok=True)
    core = str(closure.metadata["core"])
    source = None
    source_core = (
        "CORE-1" if core in {"CORE-2", "CORE-3"}
        else "CORE-4" if core == "CORE-5B"
        else None
    )
    if source_core is not None:
        raw_source = sources.get(source_core)
        if raw_source is None:
            raise RTA4FormalAggregationError(
                f"{core} aggregation requires the validated {source_core} source bundle"
            )
        source = refresh_validated_closure(
            raw_source, require_complete=True,
            allow_test_authorization=allow_test_authorization,
        )
        if source.metadata["core"] != source_core:
            raise RTA4FormalAggregationError(
                f"aggregate source bundle is not {source_core}"
            )
    data: Dict[str, list[Dict[str, Any]]] = {
        "table_1_parameters.csv": _table1(closure),
    }
    if core == "CORE-1":
        data.update({
            "figure_1_rta_comparison.csv": _figure1(
                closure, bootstrap_replicates
            ),
            "table_2_rta_summary.csv": _table2(closure),
        })
    elif core == "CORE-2":
        assert source is not None
        data["figure_2_ablation_mechanisms.csv"] = _figure2(closure, source)
    elif core == "CORE-3":
        assert source is not None
        data.update({
            "figure_3_rta_simulation_audit.csv": _figure3(closure, source),
            "table_3_simulation_audit.csv": _table3(closure),
        })
    elif core == "CORE-4":
        data["figure_4_sensitivity.csv"] = _figure4(
            closure, bootstrap_replicates
        )
    else:
        data["figure_5_scalability.csv"] = _figure5(closure)
    if set(data) != set(CORE_AGGREGATE_TABLES[core]):
        raise RTA4FormalAggregationError("core-specific aggregate routing drift")
    file_hashes = {}
    for filename, rows in data.items():
        write_csv(output_root / filename, AGGREGATE_TABLES[filename], rows)
        file_hashes[filename] = hashlib.sha256((output_root / filename).read_bytes()).hexdigest()
    manifest = {
        "aggregate_version": RTA4_AGGREGATE_VERSION,
        "core": core,
        "execution_class": closure.metadata["execution_class"],
        "authorization_id": closure.metadata.get("authorization_id"),
        "command_manifest": (
            None
            if closure.metadata.get("authorization_id") is None
            else load_strict_json(
                Path(root) / FORMAL_AUTHORIZATION_EVIDENCE
            )["command_manifest"]
        ),
        "input_closure_sha256": closure.closure_sha256,
        "trusted_source_bundles": (
            {} if source is None else {
                str(source_core): {
                    "closure_sha256": source.closure_sha256,
                    "plan_sha256": source.metadata["plan_sha256"],
                    "config_semantic_hash": source.metadata["config_semantic_hash"],
                }
            }
        ),
        "schema_sha256": closure.metadata["schema_sha256"],
        "plan_sha256": closure.metadata["plan_sha256"],
        "config_semantic_hash": closure.metadata["config_semantic_hash"],
        "bootstrap_contract": RTA4_BOOTSTRAP_CONTRACT,
        "bootstrap_cluster": "taskset_skeleton_id",
        "bootstrap_replicates": bootstrap_replicates,
        "bootstrap_seed": RTA4_BOOTSTRAP_SEED,
        "confidence_level": "19/20",
        "data_file_sha256": file_hashes,
    }
    aggregate_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    manifest["aggregate_sha256"] = aggregate_hash
    atomic_write_json(output_root / RTA4_AGGREGATE_MANIFEST, manifest)
    return manifest


def validate_aggregate_bundle(root: Path | str) -> Mapping[str, Any]:
    root = Path(root)
    try:
        manifest = load_strict_json(root / RTA4_AGGREGATE_MANIFEST)
    except Exception as exc:
        raise RTA4FormalAggregationError("cannot read aggregate manifest") from exc
    if not isinstance(manifest, Mapping) or manifest.get("aggregate_version") != RTA4_AGGREGATE_VERSION:
        raise RTA4FormalAggregationError("aggregate version mismatch")
    core = manifest.get("core")
    if core not in CORE_AGGREGATE_TABLES:
        raise RTA4FormalAggregationError("aggregate core/domain mismatch")
    hashes = manifest.get("data_file_sha256")
    expected_files = set(CORE_AGGREGATE_TABLES[str(core)])
    if not isinstance(hashes, Mapping) or set(hashes) != expected_files:
        raise RTA4FormalAggregationError("aggregate file set mismatch")
    for filename in CORE_AGGREGATE_TABLES[str(core)]:
        columns = AGGREGATE_TABLES[filename]
        path = root / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[filename]:
            raise RTA4FormalAggregationError(f"aggregate data hash mismatch: {filename}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            if next(csv.reader(handle), None) != list(columns):
                raise RTA4FormalAggregationError(f"aggregate exact header mismatch: {filename}")
    material = dict(manifest)
    observed_hash = material.pop("aggregate_sha256", None)
    if hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest() != observed_hash:
        raise RTA4FormalAggregationError("aggregate manifest identity mismatch")
    return manifest


__all__ = [
    "AGGREGATE_TABLES", "CORE_AGGREGATE_TABLES", "RTA4_AGGREGATE_MANIFEST", "RTA4_AGGREGATE_VERSION",
    "RTA4FormalAggregationError", "aggregate_formal_run",
    "cluster_bootstrap_binary", "validate_aggregate_bundle",
]
