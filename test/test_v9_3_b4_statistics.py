import math

from experiments.v9_3.performance_statistics import (
    holm_adjust, paired_permutation_test, stratified_paired_bootstrap,
    wilson_interval,
)


def test_wilson_known_values():
    low, high = wilson_interval(5, 10)
    assert math.isclose(low, 0.236593, abs_tol=1e-6)
    assert math.isclose(high, 0.763407, abs_tol=1e-6)
    assert wilson_interval(0, 0) == (None, None)


def test_paired_bootstrap_is_seeded_and_stratified():
    pairs = [
        {"u_norm": "a", "left": 1, "right": 0},
        {"u_norm": "a", "left": 0, "right": 0},
        {"u_norm": "b", "left": 0, "right": 1},
        {"u_norm": "b", "left": 0, "right": 0},
    ]
    one = stratified_paired_bootstrap(pairs, seed=3, resamples=100)
    two = stratified_paired_bootstrap(pairs, seed=3, resamples=100)
    assert one == two and one["effective_paired_n"] == 4


def test_permutation_plus_one_and_holm():
    result = paired_permutation_test([1, 1, 1], seed=2, resamples=100)
    assert result["p_value"] == (result["extreme_count"] + 1) / 101
    assert holm_adjust([0.01, 0.04, 0.03, 0.2]) == (0.04, 0.09, 0.09, 0.2)
