"""Dependency-light deterministic paired statistics for EXT-1B.

The inferential unit is one ``paired_instance_id`` within one scenario cell.
Job, task, bypass-event, and bypass-episode rows never enter this module's
bootstrap. Sampling paired differences therefore preserves all observations
clustered inside a taskset and never mixes the six B1 U-by-eta cells.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import math
import random
import statistics
from typing import Any, Dict, Iterable, Mapping, Sequence

from .config import domain_hash
from .scheduler_registry import SCHEDULER_IDS


PRIMARY_SCHEDULER = "gpfp_asap_block"
BINARY_METRICS = ("overall_success", "top_m_success")
CONTINUOUS_METRICS = (
    "top_m_max_response_time",
    "first_missed_priority_rank_numeric",
    "energy_blocked_ticks",
    "processor_wait_ticks",
    "synchronization_wait_ticks",
    "bypass_count",
)
HIGHER_IS_BETTER = {"first_missed_priority_rank_numeric"}
UNAVAILABLE = "UNAVAILABLE"
FLOAT_TIE_TOLERANCE = 1e-9


STATISTIC_COLUMNS = (
    "scenario_kind", "scenario_subtype", "scenario_cell_id", "metric_type",
    "metric", "primary_scheduler", "comparator_scheduler", "paired_count",
    "primary_only_success", "comparator_only_success", "both_success",
    "both_failure", "risk_difference", "median_primary", "median_comparator",
    "median_paired_difference", "mean_paired_difference", "ci95_lower",
    "ci95_upper", "mcnemar_exact_p", "holm_adjusted_p", "wins", "ties",
    "losses", "bootstrap_seed", "bootstrap_resamples", "holm_family",
)
B3_STATISTIC_COLUMNS = (
    *STATISTIC_COLUMNS[:3],
    "normalized_utilization",
    *STATISTIC_COLUMNS[3:],
)


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def exact_mcnemar_p(primary_only: int, comparator_only: int) -> float:
    """Two-sided exact McNemar p-value using the binomial conditional test."""

    discordant = primary_only + comparator_only
    if discordant == 0:
        return 1.0
    lower = min(primary_only, comparator_only)
    tail = sum(math.comb(discordant, index) for index in range(lower + 1))
    return min(1.0, 2.0 * tail / (2 ** discordant))


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Return Holm step-down adjusted p-values in original order."""

    count = len(p_values)
    order = sorted(range(count), key=lambda index: (p_values[index], index))
    adjusted = [1.0] * count
    running = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (count - rank) * p_values[index])
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def _bootstrap_seed(base_seed: int, material: Mapping[str, Any]) -> int:
    digest = domain_hash(
        "ASAP_BLOCK:V9.3:EXT1B:PAIRED_BOOTSTRAP_SEED:v1",
        {"base_seed": base_seed, **dict(material)},
    )
    return int(digest[:16], 16)


def paired_bootstrap_ci(
    differences: Sequence[float],
    *,
    seed: int,
    resamples: int,
    statistic: str,
) -> tuple[float, float]:
    if not differences:
        raise ValueError("paired bootstrap requires at least one pair")
    rng = random.Random(seed)
    sample_count = len(differences)
    values = []
    for _ in range(resamples):
        sampled = [differences[rng.randrange(sample_count)] for _ in range(sample_count)]
        if statistic == "mean":
            values.append(sum(sampled) / sample_count)
        elif statistic == "median":
            values.append(float(statistics.median(sampled)))
        else:
            raise ValueError("unknown paired bootstrap statistic")
    return _percentile(values, 0.025), _percentile(values, 0.975)


def _binary(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"TRUE", "1"}:
        return True
    if text in {"FALSE", "0"}:
        return False
    return None


def _number(value: Any) -> float | None:
    if value in {None, "", UNAVAILABLE, "NONE"}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _comparison_eligible(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("status")) in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
        and _binary(row.get("comparison_eligible")) is True
    )


def _ordered_counts(
    pairs: Sequence[tuple[float, float]], *, higher_is_better: bool,
) -> tuple[int, int, int]:
    wins = losses = ties = 0
    for left, right in pairs:
        if math.isclose(left, right, rel_tol=0.0, abs_tol=FLOAT_TIE_TOLERANCE):
            ties += 1
        elif (left > right) == higher_is_better:
            wins += 1
        else:
            losses += 1
    return wins, ties, losses


def _empty_row(
    context: Mapping[str, Any], metric_type: str, metric: str,
    primary: str, comparator: str,
) -> Dict[str, Any]:
    row = {
        "scenario_kind": context["scenario_kind"],
        "scenario_subtype": context["scenario_subtype"],
        "scenario_cell_id": context["scenario_cell_id"],
        "metric_type": metric_type,
        "metric": metric,
        "primary_scheduler": primary,
        "comparator_scheduler": comparator,
        **{column: UNAVAILABLE for column in STATISTIC_COLUMNS if column not in {
            "scenario_kind", "scenario_subtype", "scenario_cell_id", "metric_type",
            "metric", "primary_scheduler", "comparator_scheduler",
        }},
    }
    if context["scenario_kind"] == "TIMING_STRESS":
        row["normalized_utilization"] = context["normalized_utilization"]
    return row


def paired_statistics_rows(
    results: Iterable[Mapping[str, Any]],
    *,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    scheduler_ids: Sequence[str] = SCHEDULER_IDS,
) -> list[Dict[str, Any]]:
    selected_scheduler_ids = tuple(scheduler_ids)
    if PRIMARY_SCHEDULER not in selected_scheduler_ids:
        return []
    result_rows = list(results)
    timing_only = bool(result_rows) and {
        str(row.get("scenario_kind")) for row in result_rows
    } == {"TIMING_STRESS"}
    comparisons = (
        tuple(combinations(selected_scheduler_ids, 2))
        if timing_only
        else tuple(
            (PRIMARY_SCHEDULER, scheduler)
            for scheduler in selected_scheduler_ids
            if scheduler != PRIMARY_SCHEDULER
        )
    )
    grouped: Dict[
        tuple[tuple[str, str, str], str],
        Dict[str, Mapping[str, Any]],
    ] = defaultdict(dict)
    context_by_cell: Dict[tuple[str, str, str], Dict[str, str]] = {}
    for row in result_rows:
        cell = str(row["scenario_cell_id"])
        cell_key = (
            (
                cell,
                str(row.get("normalized_utilization", "")),
                str(row.get("scenario_subtype", "")),
            )
            if timing_only
            else (cell, "", "")
        )
        pair = str(row["paired_instance_id"])
        scheduler = str(row["scheduler_id"])
        if scheduler in grouped[(cell_key, pair)]:
            raise RuntimeError(f"duplicate EXT-1B statistic row for {pair}/{scheduler}")
        grouped[(cell_key, pair)][scheduler] = row
        context_by_cell[cell_key] = {
            "scenario_kind": str(row["scenario_kind"]),
            "scenario_subtype": str(row["scenario_subtype"]),
            "scenario_cell_id": cell,
            "normalized_utilization": cell_key[1],
        }

    rows: list[Dict[str, Any]] = []
    binary_family_indexes: Dict[
        tuple[tuple[str, str, str], str], list[int]
    ] = defaultdict(list)
    for cell_key in sorted(context_by_cell):
        context = context_by_cell[cell_key]
        pair_groups = [
            members for (group_key, _), members in grouped.items()
            if group_key == cell_key
        ]
        for metric in BINARY_METRICS:
            family = (
                f"{cell_key}:{metric}:"
                + ",".join(f"{left}-vs-{right}" for left, right in comparisons)
            )
            for primary, comparator in comparisons:
                row = _empty_row(
                    context, "BINARY", metric, primary, comparator
                )
                pairs = []
                for members in pair_groups:
                    if primary not in members or comparator not in members:
                        continue
                    if not all(_comparison_eligible(members[scheduler]) for scheduler in (
                        primary, comparator,
                    )):
                        continue
                    left = _binary(members[primary].get(metric))
                    right = _binary(members[comparator].get(metric))
                    if left is not None and right is not None:
                        pairs.append((left, right))
                primary_only = sum(left and not right for left, right in pairs)
                comparator_only = sum(right and not left for left, right in pairs)
                both = sum(left and right for left, right in pairs)
                neither = sum(not left and not right for left, right in pairs)
                row.update({
                    "paired_count": len(pairs),
                    "primary_only_success": primary_only,
                    "comparator_only_success": comparator_only,
                    "both_success": both,
                    "both_failure": neither,
                    "wins": primary_only,
                    "ties": both + neither,
                    "losses": comparator_only,
                    "bootstrap_seed": bootstrap_seed,
                    "bootstrap_resamples": bootstrap_resamples,
                    "holm_family": family,
                })
                if pairs:
                    differences = [float(left) - float(right) for left, right in pairs]
                    derived_seed = _bootstrap_seed(bootstrap_seed, {
                        "cell": cell_key, "metric": metric,
                        "primary": primary, "comparator": comparator,
                    })
                    lower, upper = paired_bootstrap_ci(
                        differences, seed=derived_seed,
                        resamples=bootstrap_resamples, statistic="mean",
                    )
                    row.update({
                        "risk_difference": sum(differences) / len(differences),
                        "ci95_lower": lower,
                        "ci95_upper": upper,
                        "mcnemar_exact_p": exact_mcnemar_p(primary_only, comparator_only),
                    })
                rows.append(row)
                binary_family_indexes[(cell_key, metric)].append(len(rows) - 1)

        for metric in CONTINUOUS_METRICS:
            for primary, comparator in comparisons:
                row = _empty_row(
                    context, "CONTINUOUS_OR_ORDERED", metric,
                    primary, comparator,
                )
                pairs = []
                for members in pair_groups:
                    if primary not in members or comparator not in members:
                        continue
                    if not all(_comparison_eligible(members[scheduler]) for scheduler in (
                        primary, comparator,
                    )):
                        continue
                    left = _number(members[primary].get(metric))
                    right = _number(members[comparator].get(metric))
                    if left is not None and right is not None:
                        pairs.append((left, right))
                row.update({
                    "paired_count": len(pairs),
                    "bootstrap_seed": bootstrap_seed,
                    "bootstrap_resamples": bootstrap_resamples,
                    "holm_family": "NOT_APPLICABLE_CONTINUOUS",
                })
                if pairs:
                    differences = [left - right for left, right in pairs]
                    derived_seed = _bootstrap_seed(bootstrap_seed, {
                        "cell": cell_key, "metric": metric,
                        "primary": primary, "comparator": comparator,
                    })
                    lower, upper = paired_bootstrap_ci(
                        differences, seed=derived_seed,
                        resamples=bootstrap_resamples, statistic="median",
                    )
                    wins, ties, losses = _ordered_counts(
                        pairs, higher_is_better=metric in HIGHER_IS_BETTER,
                    )
                    row.update({
                        "median_primary": statistics.median(left for left, _ in pairs),
                        "median_comparator": statistics.median(right for _, right in pairs),
                        "median_paired_difference": statistics.median(differences),
                        "mean_paired_difference": sum(differences) / len(differences),
                        "ci95_lower": lower,
                        "ci95_upper": upper,
                        "wins": wins,
                        "ties": ties,
                        "losses": losses,
                    })
                rows.append(row)

    for indexes in binary_family_indexes.values():
        available = [
            index for index in indexes
            if rows[index]["mcnemar_exact_p"] != UNAVAILABLE
        ]
        raw = [float(rows[index]["mcnemar_exact_p"]) for index in available]
        for index, adjusted in zip(available, holm_adjust(raw)):
            rows[index]["holm_adjusted_p"] = adjusted
    return rows
