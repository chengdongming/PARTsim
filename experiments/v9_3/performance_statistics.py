"""Paired-taskset inference used by the four B4 paper figures."""

from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


UNAVAILABLE = "UNAVAILABLE"


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> Tuple[Optional[float], Optional[float]]:
    if successes < 0 or total < 0 or successes > total:
        raise ValueError("invalid binomial counts")
    if total == 0:
        return None, None
    if not math.isclose(confidence, 0.95, rel_tol=0, abs_tol=1e-12):
        raise ValueError("B4 freezes Wilson confidence at 95%")
    z = 1.959963984540054
    p_value = successes / total
    denominator = 1 + z * z / total
    centre = (p_value + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p_value * (1 - p_value) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires samples")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def stratified_paired_bootstrap(
    pairs: Sequence[Mapping[str, Any]], *, seed: int, resamples: int = 10000,
    stratum_key: str = "u_norm", left_key: str = "left", right_key: str = "right",
) -> Dict[str, Any]:
    if resamples <= 0:
        raise ValueError("resamples must be positive")
    strata = defaultdict(list)
    for pair in pairs:
        left, right = pair.get(left_key), pair.get(right_key)
        if left is None or right is None or left == UNAVAILABLE or right == UNAVAILABLE:
            raise ValueError("paired bootstrap cannot silently drop UNAVAILABLE values")
        strata[str(pair[stratum_key])].append(float(left) - float(right))
    if not strata or any(not values for values in strata.values()):
        raise ValueError("paired bootstrap requires complete strata")
    estimate = sum(sum(values) / len(values) for values in strata.values()) / len(strata)
    rng = random.Random(seed)
    samples = []
    ordered_strata = sorted(strata)
    for _ in range(resamples):
        stratum_means = []
        for key in ordered_strata:
            values = strata[key]
            draw = [values[rng.randrange(len(values))] for _ in range(len(values))]
            stratum_means.append(sum(draw) / len(draw))
        samples.append(sum(stratum_means) / len(stratum_means))
    return {
        "estimate": estimate, "ci_low": _percentile(samples, 0.025),
        "ci_high": _percentile(samples, 0.975),
        "effective_paired_n": sum(len(values) for values in strata.values()),
        "strata": ordered_strata, "resamples": resamples, "seed": seed,
    }


def paired_permutation_test(differences: Sequence[float], *, seed: int, resamples: int = 10000) -> Dict[str, Any]:
    values = [float(value) for value in differences]
    if not values:
        raise ValueError("paired permutation requires complete pairs")
    observed = sum(values) / len(values)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(resamples):
        permuted = sum(value if rng.getrandbits(1) else -value for value in values) / len(values)
        if abs(permuted) >= abs(observed) - 1e-15:
            extreme += 1
    return {
        "estimate": observed, "p_value": (extreme + 1) / (resamples + 1),
        "extreme_count": extreme, "resamples": resamples, "seed": seed,
        "paired_n": len(values),
    }


def holm_adjust(p_values: Sequence[float]) -> Tuple[float, ...]:
    if not p_values:
        return tuple()
    if any(value < 0 or value > 1 for value in p_values):
        raise ValueError("p-values must lie in [0,1]")
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, (index, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[index] = running
    return tuple(adjusted)


def complete_paired_groups(
    rows: Iterable[Mapping[str, Any]], *, group_keys: Sequence[str], scheduler_key: str,
    required_schedulers: Sequence[str],
) -> Dict[Tuple[Any, ...], Dict[str, Mapping[str, Any]]]:
    groups = defaultdict(dict)
    for row in rows:
        key = tuple(row[name] for name in group_keys)
        scheduler = str(row[scheduler_key])
        if scheduler in groups[key]:
            raise ValueError(f"duplicate scheduler result in paired group: {key}, {scheduler}")
        groups[key][scheduler] = row
    required = set(required_schedulers)
    for key, group in groups.items():
        if set(group) != required:
            raise ValueError(f"incomplete paired scheduler group: {key}")
    return dict(groups)
