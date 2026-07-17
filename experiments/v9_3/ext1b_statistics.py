"""Dependency-light deterministic paired statistics for EXT-1B."""

from __future__ import annotations

from collections import defaultdict
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


STATISTIC_COLUMNS = (
    "scenario_kind", "scenario_subtype", "scenario_cell_id", "metric_type",
    "metric", "primary_scheduler", "comparator_scheduler", "paired_count",
    "primary_only_success", "comparator_only_success", "both_success",
    "both_failure", "risk_difference", "median_primary", "median_comparator",
    "median_paired_difference", "mean_paired_difference", "ci95_lower",
    "ci95_upper", "mcnemar_exact_p", "holm_adjusted_p", "wins", "ties",
    "losses", "bootstrap_seed", "bootstrap_resamples", "holm_family",
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


def _empty_row(context: Mapping[str, Any], metric_type: str, metric: str, comparator: str) -> Dict[str, Any]:
    return {
        "scenario_kind": context["scenario_kind"],
        "scenario_subtype": context["scenario_subtype"],
        "scenario_cell_id": context["scenario_cell_id"],
        "metric_type": metric_type,
        "metric": metric,
        "primary_scheduler": PRIMARY_SCHEDULER,
        "comparator_scheduler": comparator,
        **{column: UNAVAILABLE for column in STATISTIC_COLUMNS if column not in {
            "scenario_kind", "scenario_subtype", "scenario_cell_id", "metric_type",
            "metric", "primary_scheduler", "comparator_scheduler",
        }},
    }


def paired_statistics_rows(
    results: Iterable[Mapping[str, Any]],
    *,
    bootstrap_seed: int,
    bootstrap_resamples: int,
) -> list[Dict[str, Any]]:
    grouped: Dict[tuple[str, str], Dict[str, Mapping[str, Any]]] = defaultdict(dict)
    context_by_cell: Dict[str, Dict[str, str]] = {}
    for row in results:
        cell = str(row["scenario_cell_id"])
        pair = str(row["paired_instance_id"])
        grouped[(cell, pair)][str(row["scheduler_id"])] = row
        context_by_cell[cell] = {
            "scenario_kind": str(row["scenario_kind"]),
            "scenario_subtype": str(row["scenario_subtype"]),
            "scenario_cell_id": cell,
        }

    rows: list[Dict[str, Any]] = []
    binary_family_indexes: Dict[tuple[str, str], list[int]] = defaultdict(list)
    for cell in sorted(context_by_cell):
        context = context_by_cell[cell]
        pair_groups = [members for (cell_id, _), members in grouped.items() if cell_id == cell]
        for metric in BINARY_METRICS:
            family = f"{cell}:{metric}:ASAP-BLOCK-vs-eight"
            for comparator in SCHEDULER_IDS:
                if comparator == PRIMARY_SCHEDULER:
                    continue
                row = _empty_row(context, "BINARY", metric, comparator)
                pairs = []
                for members in pair_groups:
                    if PRIMARY_SCHEDULER not in members or comparator not in members:
                        continue
                    left = _binary(members[PRIMARY_SCHEDULER].get(metric))
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
                        "cell": cell, "metric": metric, "comparator": comparator,
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
                binary_family_indexes[(cell, metric)].append(len(rows) - 1)

        for metric in CONTINUOUS_METRICS:
            for comparator in SCHEDULER_IDS:
                if comparator == PRIMARY_SCHEDULER:
                    continue
                row = _empty_row(context, "CONTINUOUS_OR_ORDERED", metric, comparator)
                pairs = []
                for members in pair_groups:
                    if PRIMARY_SCHEDULER not in members or comparator not in members:
                        continue
                    left = _number(members[PRIMARY_SCHEDULER].get(metric))
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
                        "cell": cell, "metric": metric, "comparator": comparator,
                    })
                    lower, upper = paired_bootstrap_ci(
                        differences, seed=derived_seed,
                        resamples=bootstrap_resamples, statistic="median",
                    )
                    if metric in HIGHER_IS_BETTER:
                        wins = sum(left > right for left, right in pairs)
                        losses = sum(left < right for left, right in pairs)
                    else:
                        wins = sum(left < right for left, right in pairs)
                        losses = sum(left > right for left, right in pairs)
                    row.update({
                        "median_primary": statistics.median(left for left, _ in pairs),
                        "median_comparator": statistics.median(right for _, right in pairs),
                        "median_paired_difference": statistics.median(differences),
                        "mean_paired_difference": sum(differences) / len(differences),
                        "ci95_lower": lower,
                        "ci95_upper": upper,
                        "wins": wins,
                        "ties": sum(left == right for left, right in pairs),
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
