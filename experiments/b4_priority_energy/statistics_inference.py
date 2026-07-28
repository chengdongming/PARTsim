#!/usr/bin/env python3
"""Deterministic cluster-level inference for B4-PE I5D."""

from __future__ import annotations

import hashlib
import math

import numpy as np


class InferenceError(ValueError):
    """An estimand or inference request violates the frozen contract."""


def safe_ratio(numerator, denominator):
    """Return a finite ratio, preserving a zero denominator as ``None``."""
    if isinstance(numerator, bool) or isinstance(denominator, bool):
        raise InferenceError("ratio operands must be numeric")
    if not isinstance(numerator, (int, float)) or not isinstance(
        denominator, (int, float)
    ):
        raise InferenceError("ratio operands must be numeric")
    if not math.isfinite(float(numerator)) or not math.isfinite(float(denominator)):
        raise InferenceError("ratio operands must be finite")
    if numerator < 0 or denominator < 0:
        raise InferenceError("ratio operands must be nonnegative")
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def finite_mean(values):
    defined = [float(value) for value in values if value is not None]
    if not defined:
        return None
    if not all(math.isfinite(value) for value in defined):
        raise InferenceError("mean input is non-finite")
    return math.fsum(defined) / len(defined)


def equal_strata_mean(values_by_stratum):
    """Mean within each nonempty stratum and then equally across strata."""
    means = [finite_mean(values) for _, values in sorted(values_by_stratum.items())]
    return finite_mean(means)


def derive_seed_identity(contract_sha256, root_seed, analysis_name, identity):
    material = f"{contract_sha256}{root_seed}{analysis_name}{identity}".encode(
        "utf-8"
    )
    return hashlib.sha256(material).hexdigest()


def _rng(seed_identity):
    if (
        not isinstance(seed_identity, str)
        or len(seed_identity) != 64
        or any(ch not in "0123456789abcdef" for ch in seed_identity)
    ):
        raise InferenceError("invalid seed identity")
    return np.random.Generator(np.random.PCG64(int(seed_identity, 16)))


def _linear_percentile_interval(estimates, confidence_level):
    alpha = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(
        estimates,
        [alpha, 1.0 - alpha],
        interpolation="linear",
    )
    return float(lower), float(upper)


def percentile_stratified_bootstrap(
    values_by_stratum,
    replicates,
    seed_identity,
    confidence_level=0.95,
):
    """Bootstrap whole clusters within strata and average strata equally."""
    if type(replicates) is not int or replicates <= 0:
        raise InferenceError("bootstrap replicates must be positive")
    if not 0.0 < confidence_level < 1.0:
        raise InferenceError("invalid confidence level")
    arrays = []
    normalized = {}
    for stratum, values in sorted(values_by_stratum.items()):
        if not values:
            raise InferenceError("bootstrap stratum is empty")
        array = np.array(
            [np.nan if value is None else float(value) for value in values],
            dtype=np.float64,
        )
        finite = np.isfinite(array)
        if not np.all(finite | np.isnan(array)):
            raise InferenceError("bootstrap input is non-finite")
        if not np.any(finite):
            raise InferenceError("bootstrap stratum has no defined values")
        arrays.append(array)
        normalized[stratum] = [
            None if np.isnan(value) else float(value) for value in array
        ]
    if not arrays:
        return {
            "point_estimate": None,
            "ci_lower": None,
            "ci_upper": None,
            "bootstrap_replicates": replicates,
            "seed_identity": seed_identity,
        }
    point = equal_strata_mean(normalized)
    finite_values = np.concatenate([array[np.isfinite(array)] for array in arrays])
    if np.all(finite_values == finite_values[0]):
        constant = float(finite_values[0])
        lower, upper = _linear_percentile_interval(
            np.full(replicates, constant, dtype=np.float64),
            confidence_level,
        )
        return {
            "point_estimate": point,
            "ci_lower": lower,
            "ci_upper": upper,
            "bootstrap_replicates": replicates,
            "seed_identity": seed_identity,
        }
    rng = _rng(seed_identity)
    estimates = np.empty(replicates, dtype=np.float64)
    offset = 0
    while offset < replicates:
        count = min(1024, replicates - offset)
        stratum_means = []
        for array in arrays:
            sample = array[rng.integers(0, len(array), size=(count, len(array)))]
            finite = np.isfinite(sample)
            totals = np.where(finite, sample, 0.0).sum(axis=1)
            denominators = finite.sum(axis=1)
            if np.any(denominators == 0):
                raise InferenceError("bootstrap resample has no defined value")
            stratum_means.append(totals / denominators)
        estimates[offset : offset + count] = np.mean(stratum_means, axis=0)
        offset += count
    lower, upper = _linear_percentile_interval(estimates, confidence_level)
    return {
        "point_estimate": point,
        "ci_lower": lower,
        "ci_upper": upper,
        "bootstrap_replicates": replicates,
        "seed_identity": seed_identity,
    }


def paired_sign_flip(
    differences_by_stratum,
    random_draws,
    seed_identity,
    exact_max_nonzero=20,
):
    """Two-sided paired sign-flip test for the equal-strata estimand."""
    values = []
    weights = []
    for _, differences in sorted(differences_by_stratum.items()):
        finite = [float(value) for value in differences]
        if not finite:
            raise InferenceError("sign-flip stratum is empty")
        if not all(math.isfinite(value) for value in finite):
            raise InferenceError("sign-flip input is non-finite")
        stratum_weight = 1.0 / len(differences_by_stratum) / len(finite)
        for value in finite:
            if value != 0.0:
                values.append(value)
                weights.append(stratum_weight)
    nonzero_count = len(values)
    observed = abs(math.fsum(w * value for w, value in zip(weights, values)))
    tolerance = max(1e-15, observed * 1e-14)
    if nonzero_count == 0:
        return {
            "raw_p": 1.0,
            "method": "exact_sign_flip",
            "observed_statistic": observed,
            "enumerated_permutations": 1,
            "extreme_count": 1,
            "p_value_numerator": 1,
            "p_value_denominator": 1,
            "plus_one_correction": False,
            "seed_identity": seed_identity,
            "nonzero_cluster_count": 0,
        }
    magnitudes = np.abs(np.asarray(values, dtype=np.float64))
    weighted = magnitudes * np.asarray(weights, dtype=np.float64)
    if nonzero_count <= exact_max_nonzero:
        total = 1 << nonzero_count
        extreme = 0
        for start in range(0, total, 65536):
            stop = min(total, start + 65536)
            codes = np.arange(start, stop, dtype=np.uint64)[:, None]
            bit_positions = np.arange(nonzero_count, dtype=np.uint64)[None, :]
            signs = np.where(((codes >> bit_positions) & 1) == 0, -1.0, 1.0)
            statistics = np.abs(signs @ weighted)
            extreme += int(np.count_nonzero(statistics >= observed - tolerance))
        return {
            "raw_p": extreme / total,
            "method": "exact_sign_flip",
            "observed_statistic": observed,
            "enumerated_permutations": total,
            "extreme_count": extreme,
            "p_value_numerator": extreme,
            "p_value_denominator": total,
            "plus_one_correction": False,
            "seed_identity": seed_identity,
            "nonzero_cluster_count": nonzero_count,
        }
    if type(random_draws) is not int or random_draws <= 0:
        raise InferenceError("random draws must be positive")
    rng = _rng(seed_identity)
    random_extreme_count = 0
    remaining = random_draws
    while remaining:
        count = min(4096, remaining)
        signs = np.where(
            rng.integers(0, 2, size=(count, nonzero_count), dtype=np.int8) == 0,
            -1.0,
            1.0,
        )
        statistics = np.abs(signs @ weighted)
        random_extreme_count += int(
            np.count_nonzero(statistics >= observed - tolerance)
        )
        remaining -= count
    numerator = random_extreme_count + 1
    denominator = random_draws + 1
    return {
        "raw_p": numerator / denominator,
        "method": "monte_carlo_sign_flip",
        "observed_statistic": observed,
        "random_draws": random_draws,
        "observed_permutation_forced_in_draws": False,
        "observed_permutation_accounting": "plus_one_only",
        "random_extreme_count": random_extreme_count,
        "p_value_numerator": numerator,
        "p_value_denominator": denominator,
        "plus_one_correction": True,
        "seed_identity": seed_identity,
        "nonzero_cluster_count": nonzero_count,
    }


def holm_step_down(raw_p_by_name, comparison_order, alpha=0.05):
    """Stable Holm correction while preserving the frozen display order."""
    if set(raw_p_by_name) != set(comparison_order):
        raise InferenceError("Holm comparison family mismatch")
    ordered = sorted(
        comparison_order,
        key=lambda name: (float(raw_p_by_name[name]), comparison_order.index(name)),
    )
    adjusted = {}
    ranks = {}
    running = 0.0
    family_size = len(ordered)
    for index, name in enumerate(ordered):
        raw = float(raw_p_by_name[name])
        if not 0.0 <= raw <= 1.0 or not math.isfinite(raw):
            raise InferenceError("invalid raw p-value")
        running = max(running, min(1.0, (family_size - index) * raw))
        adjusted[name] = running
        ranks[name] = index + 1
    return {
        name: {
            "raw_p": float(raw_p_by_name[name]),
            "holm_adjusted_p": adjusted[name],
            "holm_rank": ranks[name],
            "reject_at_0_05": adjusted[name] <= alpha,
        }
        for name in comparison_order
    }
