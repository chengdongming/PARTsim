import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


B4_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B4_DIR))

import statistics_common as statistics
import statistics_inference as inference


def _sha(material):
    return hashlib.sha256(material).hexdigest()


def _case_passes(algorithm, cluster_index):
    hp = {
        "ASAP-BLOCK": True,
        "ASAP-NONBLOCK": False,
        "ASAP-SYNC": cluster_index % 2 == 0,
        "ALAP-BLOCK": True,
        "ALAP-NONBLOCK": cluster_index < 2,
        "ALAP-SYNC": cluster_index != 3,
        "ST-BLOCK": cluster_index < 3,
        "ST-NONBLOCK": cluster_index == 0,
        "ST-SYNC": cluster_index % 2 == 1,
    }[algorithm]
    if algorithm == "ASAP-BLOCK":
        whole = cluster_index % 2 == 0
    elif algorithm == "ASAP-NONBLOCK":
        whole = False
    else:
        whole = hp and cluster_index == 0
    return hp, whole


def _synthetic_rows():
    cases = []
    tasks = []
    identity_fields = tuple(statistics.ANALYSIS_CONTRACT["case_identity_field_order"])
    utilizations = ("0.2", "0.4")
    lambdas = ("0.70", "1.00")
    for utilization in utilizations:
        for cluster_index in range(4):
            taskset = _sha(f"taskset:{utilization}:{cluster_index}".encode())
            for lam in lambdas:
                pairing_dimensions = {
                    "phase": "formal_main",
                    "utilization": utilization,
                    "taskset_id": taskset,
                    "lambda_E": lam,
                    "rho_E": "2",
                }
                pairing_key = _sha(statistics.compact_json(pairing_dimensions).encode())
                for algorithm_index, algorithm in enumerate(statistics.ALGORITHMS):
                    hp_pass, whole_pass = _case_passes(algorithm, cluster_index)
                    case_id = _sha(f"case:{utilization}:{cluster_index}:{lam}:{algorithm}".encode())
                    row = {name: None for name in statistics.CASE_FIELDS}
                    row.update(
                        {
                            "analysis_schema_version": 2,
                            "phase": "formal_main",
                            "case_id": case_id,
                            "pairing_key": pairing_key,
                            "pairing_dimensions": pairing_dimensions,
                            "taskset_id": taskset,
                            "taskset_semantic_hash": taskset,
                            "taskset_sha256": _sha((taskset + ":bytes").encode()),
                            "taskset_seed": cluster_index,
                            "replicate_index": cluster_index,
                            "taskset_pool": "synthetic",
                            "source_identity": _sha(f"source:{utilization}:{cluster_index}:{lam}".encode()),
                            "source_sha256": _sha(f"source-bytes:{utilization}:{cluster_index}:{lam}".encode()),
                            "source_seed": cluster_index + 100,
                            "source_profile": "synthetic",
                            "utilization": utilization,
                            "target_normalized_utilization": float(utilization),
                            "target_total_utilization": 4.0 * float(utilization),
                            "lambda_E": lam,
                            "rho_E": "2",
                            "E0_j": 1.0,
                            "Emax_j": 2.0,
                            "alpha_w": 0.5,
                            "E0_rule": "synthetic",
                            "Emax_rule": "synthetic",
                            "alpha_rule": "synthetic",
                            "M": 4,
                            "horizon_ms": 30000,
                            "not_for_paper": True,
                            "algorithm": algorithm,
                            "configured_scheduler": algorithm.lower(),
                            "scheduler_display_name": algorithm,
                            "scheduler_implementation": "SyntheticScheduler",
                            "scheduler_family": algorithm.split("-", 1)[0],
                            "blocking_policy": algorithm.split("-", 1)[1],
                            "algorithm_order": algorithm_index,
                            "trace_schema_version": 3,
                            "observability_summary_contract_version": 2,
                            "observability_summary_horizon_ms": 30000,
                            "processor_count": 4,
                            "task_count": 10,
                            "scheduling_outcomes": {
                                "audit_issues": [],
                                "deadline_miss_jobs": 0,
                                "terminated_jobs": 0,
                                "unfinished_at_horizon_jobs": 0,
                            },
                            "whole_pass": whole_pass,
                            "hp_pass": hp_pass,
                            "lp_pass": (not hp_pass) or whole_pass,
                            "bypass_opportunity_ticks": 10 if algorithm == "ASAP-NONBLOCK" else 0,
                            "actual_bypass_ticks": 5 if algorithm == "ASAP-NONBLOCK" else 0,
                            "low_priority_bypass_core_ticks": 2 if algorithm == "ASAP-NONBLOCK" else 0,
                            "hp_dispatch_demand_ticks": 20,
                            "hp_energy_blocked_ticks": 5 if algorithm == "ASAP-BLOCK" else 0,
                            "hp_energy_blocked_job_ticks": 5 if algorithm == "ASAP-BLOCK" else 0,
                            "observed_decision_ticks": 100,
                            "sync_batch_evaluation_ticks": 10 if algorithm == "ASAP-SYNC" else 0,
                            "sync_batch_reject_ticks": 4 if algorithm == "ASAP-SYNC" else 0,
                            "alap_deferral_opportunity_ticks": 10 if algorithm == "ALAP-BLOCK" else 0,
                            "positive_slack_deferral_ticks": 3 if algorithm == "ALAP-BLOCK" else 0,
                            "st_charging_opportunity_ticks": 10 if algorithm == "ST-BLOCK" else 0,
                            "st_slack_charging_wait_ticks": 2 if algorithm == "ST-BLOCK" else 0,
                            "offered_energy_j": 10.0,
                            "credited_energy_j": 9.0,
                            "clipped_energy_j": 1.0,
                            "consumed_energy_j": 8.0,
                            "battery_min_j": 0.1,
                            "battery_max_j": 2.0,
                            "battery_final_j": 1.0,
                            "battery_empty_ticks": 0,
                            "battery_full_ticks": 1,
                            "observed_energy_intervals": 100,
                        }
                    )
                    case_tasks = []
                    for rank in range(10):
                        miss = 0
                        if rank == 0 and not hp_pass:
                            miss = 1
                        if rank == 4 and hp_pass and not whole_pass:
                            miss = 1
                        task = {name: row[name] for name in identity_fields}
                        task.update(
                            {
                                "task_name": f"task_{rank}",
                                "priority_rank": rank,
                                "is_top4": rank < 4,
                                "is_bottom6": rank >= 4,
                                "released_jobs": 101,
                                "adjudicable_jobs": 100,
                                "completed_jobs": 101 - miss,
                                "terminated_jobs": miss,
                                "deadline_miss_jobs": miss,
                                "unfinished_at_horizon_jobs": 0,
                                "executed_core_ticks": 100,
                                "completed_response_time_count": 101 - miss,
                                "completed_response_time_sum_ms": 202 - 2 * miss,
                                "completed_response_time_max_ms": 2,
                                "task_pass": miss == 0,
                            }
                        )
                        task = {name: task[name] for name in statistics.TASK_FIELDS}
                        case_tasks.append(task)
                    for prefix, selected in (
                        ("all", case_tasks),
                        ("hp", case_tasks[:4]),
                        ("lp", case_tasks[4:]),
                    ):
                        metric_names = statistics.ANALYSIS_CONTRACT["task_metric_field_order"]
                        for name in metric_names:
                            values = [task[name] for task in selected]
                            row[f"{prefix}_{name}"] = max(values) if name == "completed_response_time_max_ms" else sum(values)
                    row["scheduling_outcomes"]["deadline_miss_jobs"] = row["all_deadline_miss_jobs"]
                    row["scheduling_outcomes"]["terminated_jobs"] = row["all_terminated_jobs"]
                    row = {name: row[name] for name in statistics.CASE_FIELDS}
                    cases.append(row)
                    tasks.extend(case_tasks)
    return cases, tasks


def write_synthetic_analysis(root):
    root.mkdir()
    cases, tasks = _synthetic_rows()
    cases_jsonl = statistics.jsonl_bytes(cases, statistics.CASE_FIELDS)
    tasks_jsonl = statistics.jsonl_bytes(tasks, statistics.TASK_FIELDS)
    cases_csv = statistics.csv_bytes(cases, statistics.CASE_FIELDS)
    tasks_csv = statistics.csv_bytes(tasks, statistics.TASK_FIELDS)
    data = {
        "cases.jsonl": cases_jsonl,
        "tasks.jsonl": tasks_jsonl,
        "cases.csv": cases_csv,
        "tasks.csv": tasks_csv,
    }
    for name, material in data.items():
        (root / name).write_bytes(material)
    pairing_count = 2 * 4 * 2
    audit = {
        "analysis_schema_version": 2,
        "overall_pass": True,
        "checks": {"jsonl_csv_parity": True},
        "case_row_count": len(cases),
        "task_row_count": len(tasks),
        "pairing_group_count": pairing_count,
        "output_file_sha256": {name: _sha(material) for name, material in data.items()},
        "issues": [],
    }
    audit_bytes = statistics.pretty_json_bytes(audit)
    (root / "analysis_audit.json").write_bytes(audit_bytes)
    manifest = {
        "analysis_schema_version": 2,
        "analysis_contract_path": "experiments/b4_priority_energy/analysis_contract_v2.json",
        "analysis_contract_sha256": statistics.file_sha256(statistics.ANALYSIS_CONTRACT_PATH),
        "observability_contract_path": "experiments/b4_priority_energy/observability_summary_contract_v2.json",
        "observability_contract_sha256": statistics.file_sha256(statistics.OBSERVABILITY_CONTRACT_PATH),
        "candidate_v3_path": "experiments/b4_priority_energy/b4_pe_freeze_candidate_v3.json",
        "candidate_v3_sha256": statistics.file_sha256(statistics.CANDIDATE_PATH),
        "source_code_commit": "synthetic-source-commit",
        "source_base_commit": "synthetic-source-commit",
        "extractor_version_sha256": "a" * 64,
        "case_row_count": len(cases),
        "task_row_count": len(tasks),
        "pairing_group_count": pairing_count,
        "algorithm_canonical_order": list(statistics.ALGORITHMS),
        "output_file_sha256": {
            **{name: _sha(material) for name, material in data.items()},
            "analysis_audit.json": _sha(audit_bytes),
        },
        "no_paper_data_generated": True,
    }
    (root / "analysis_manifest.json").write_bytes(statistics.pretty_json_bytes(manifest))
    return root


@pytest.fixture()
def synthetic_root(tmp_path):
    return write_synthetic_analysis(tmp_path / "analysis")


def test_safe_ratio_and_zero_denominator_na():
    assert inference.safe_ratio(1, 4) == 0.25
    assert inference.safe_ratio(0, 0) is None
    with pytest.raises(inference.InferenceError):
        inference.safe_ratio(math.inf, 1)


def test_cluster_key_excludes_lambda_algorithm_source_but_includes_rho(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    row = dataset["cases"][0]
    changed = copy.deepcopy(row)
    changed.update({"lambda_E": "9.99", "algorithm": "ST-SYNC", "source_identity": "changed", "source_sha256": "changed"})
    assert statistics.cluster_key(row) == statistics.cluster_key(changed)
    changed["rho_E"] = "1"
    assert statistics.cluster_key(row) != statistics.cluster_key(changed)


def test_pilot_cluster_key_keeps_both_rhos_together(synthetic_root):
    row = statistics.load_analysis(synthetic_root)["cases"][0]
    one = copy.deepcopy(row)
    two = copy.deepcopy(row)
    one.update({"phase": "pilot", "rho_E": "1"})
    two.update({"phase": "pilot", "rho_E": "2"})
    assert statistics.cluster_key(one) == statistics.cluster_key(two)


def test_constant_bootstrap_ci_degenerates_exactly():
    result = inference.percentile_stratified_bootstrap(
        {"0.2": [0.25] * 4, "0.4": [0.25] * 4}, 10000, "1" * 64
    )
    assert result["point_estimate"] == 0.25
    assert result["ci_lower"] == result["ci_upper"] == 0.25


def test_bootstrap_passes_explicit_linear_interpolation(monkeypatch):
    original_quantile = inference.np.quantile
    calls = []

    def guarded_quantile(values, quantiles, *args, **kwargs):
        assert kwargs.get("interpolation") == "linear"
        calls.append((values.copy(), list(quantiles)))
        return original_quantile(values, quantiles, *args, **kwargs)

    monkeypatch.setattr(inference.np, "quantile", guarded_quantile)
    inference.percentile_stratified_bootstrap(
        {"0.2": [0.0, 1.0]}, 8, "1" * 64
    )
    assert len(calls) == 1


def _fixed_bootstrap_result(monkeypatch):
    sample_indices = np.asarray(
        [
            [0, 0],
            [0, 1],
            [1, 1],
            [1, 1],
        ],
        dtype=np.int64,
    )

    class FixedBootstrapRng:
        def integers(self, low, high, *, size):
            assert (low, high, size) == (0, 2, (4, 2))
            return sample_indices.copy()

    monkeypatch.setattr(inference, "_rng", lambda _: FixedBootstrapRng())
    result = inference.percentile_stratified_bootstrap(
        {"0.2": [0.0, 1.0]},
        4,
        "1" * 64,
        confidence_level=0.5,
    )
    return result


def test_bootstrap_linear_interpolation_is_nondefault_sensitive(monkeypatch):
    result = _fixed_bootstrap_result(monkeypatch)
    estimates = np.asarray([0.0, 0.5, 1.0, 1.0], dtype=np.float64)
    quantiles = [0.25, 0.75]
    expected = np.quantile(estimates, quantiles, interpolation="linear")
    lower_method = np.quantile(estimates, quantiles, interpolation="lower")

    assert result["ci_lower"] == expected[0] == 0.375
    assert result["ci_upper"] == expected[1] == 1.0
    assert result["ci_lower"] != lower_method[0]
    assert result["ci_lower"] <= result["point_estimate"] <= result["ci_upper"]


def test_constant_bootstrap_uses_explicit_linear_interpolation(monkeypatch):
    original_quantile = inference.np.quantile
    calls = []

    def guarded_quantile(values, quantiles, *args, **kwargs):
        calls.append(kwargs.get("interpolation"))
        return original_quantile(values, quantiles, *args, **kwargs)

    monkeypatch.setattr(inference.np, "quantile", guarded_quantile)
    result = inference.percentile_stratified_bootstrap(
        {"0.2": [0.25] * 4, "0.4": [0.25] * 4}, 10000, "1" * 64
    )

    assert calls == ["linear"]
    assert result["point_estimate"] == result["ci_lower"] == result["ci_upper"] == 0.25


def test_bootstrap_isolated_from_numpy_quantile_default(monkeypatch):
    original_quantile = inference.np.quantile

    def reject_implicit_default(values, quantiles, *, interpolation=None):
        assert interpolation is not None
        return original_quantile(
            values,
            quantiles,
            interpolation=interpolation,
        )

    monkeypatch.setattr(inference.np, "quantile", reject_implicit_default)
    result = inference.percentile_stratified_bootstrap(
        {"0.2": [0.0, 1.0]}, 8, "1" * 64
    )
    assert result["ci_lower"] <= result["point_estimate"] <= result["ci_upper"]


def test_bootstrap_linear_matches_current_numpy_default_exactly(monkeypatch):
    result = _fixed_bootstrap_result(monkeypatch)
    estimates = np.asarray([0.0, 0.5, 1.0, 1.0], dtype=np.float64)
    quantiles = [0.25, 0.75]
    previous_default = np.quantile(estimates, quantiles)
    explicit_linear = np.quantile(
        estimates,
        quantiles,
        interpolation="linear",
    )

    assert np.array_equal(previous_default, explicit_linear)
    assert result["ci_lower"] == previous_default[0]
    assert result["ci_upper"] == previous_default[1]


def test_nonconstant_bootstrap_is_deterministic():
    values = {"0.2": [0.0, 0.25, 1.0], "0.4": [0.5, 0.75, 1.0]}
    first = inference.percentile_stratified_bootstrap(
        values, 100, "7" * 64
    )
    second = inference.percentile_stratified_bootstrap(
        values, 100, "7" * 64
    )
    assert first == second


def test_exact_sign_flip_known_constant_effect():
    result = inference.paired_sign_flip(
        {"0.2": [1.0, 1.0], "0.4": [1.0, 1.0]}, 100000, "2" * 64
    )
    assert result["method"] == "exact_sign_flip"
    assert result["enumerated_permutations"] == 16
    assert result["extreme_count"] == 2
    assert result["p_value_numerator"] == 2
    assert result["p_value_denominator"] == 16
    assert result["plus_one_correction"] is False
    assert "random_draws" not in result
    assert result["raw_p"] == pytest.approx(0.125)


def test_exact_sign_flip_zero_nonzero_clusters_uses_no_plus_one():
    result = inference.paired_sign_flip(
        {"0.2": [0.0, 0.0], "0.4": [0.0, 0.0]}, 100000, "2" * 64
    )
    assert result["method"] == "exact_sign_flip"
    assert result["nonzero_cluster_count"] == 0
    assert result["enumerated_permutations"] == 1
    assert result["extreme_count"] == 1
    assert result["plus_one_correction"] is False
    assert result["raw_p"] == 1.0


def test_monte_carlo_sign_flip_is_deterministic_and_uses_plus_one_only():
    values = {"0.2": [1.0] * 11, "0.4": [0.5] * 10}
    first = inference.paired_sign_flip(values, 100000, "3" * 64)
    second = inference.paired_sign_flip(values, 100000, "3" * 64)
    assert first == second
    assert first["method"] == "monte_carlo_sign_flip"
    assert first["random_draws"] == 100000
    assert first["observed_permutation_forced_in_draws"] is False
    assert first["observed_permutation_accounting"] == "plus_one_only"
    assert first["p_value_numerator"] == first["random_extreme_count"] + 1
    assert first["p_value_denominator"] == 100001
    assert first["raw_p"] == (
        first["p_value_numerator"] / first["p_value_denominator"]
    )
    assert 0.0 < first["raw_p"] <= 1.0


def _controlled_monte_carlo(monkeypatch, random_bits):
    width = len(random_bits[0])
    class FixedRng:
        def __init__(self):
            self.offset = 0

        def integers(self, low, high, *, size, dtype):
            assert (low, high, size[1], dtype) == (0, 2, width, inference.np.int8)
            stop = self.offset + size[0]
            result = inference.np.asarray(
                random_bits[self.offset : stop], dtype=dtype
            )
            assert len(result) == size[0]
            self.offset = stop
            return result

    rng = FixedRng()
    monkeypatch.setattr(inference, "_rng", lambda _: rng)
    result = inference.paired_sign_flip(
        {"0.2": [1.0] * width}, len(random_bits), "3" * 64
    )
    return result, rng


def _mixed_random_bits(width, count):
    return [
        ([0, 1] * (width // 2) + [index % 2])[:width]
        for index in range(count)
    ]


def test_monte_carlo_sign_flip_uses_b_random_draws_and_one_plus_one(monkeypatch):
    width = 21
    random_bits = [
        [0] * width,
        [1] * width,
        [0] + [1] * (width - 1),
        [1] + [0] * (width - 1),
        [0, 1] * 10 + [0],
    ]
    result, rng = _controlled_monte_carlo(monkeypatch, random_bits)

    assert rng.offset == 5
    assert result["random_draws"] == 5
    assert result["random_extreme_count"] == 2
    assert result["p_value_numerator"] == 3
    assert result["p_value_denominator"] == 6
    assert result["raw_p"] == pytest.approx((2 + 1) / (5 + 1))


def test_monte_carlo_no_random_extreme_has_minimum_plus_one_p(monkeypatch):
    random_bits = _mixed_random_bits(21, 5)
    result, rng = _controlled_monte_carlo(monkeypatch, random_bits)

    assert rng.offset == 5
    assert result["random_extreme_count"] == 0
    assert result["raw_p"] == pytest.approx(1 / 6)


def test_monte_carlo_all_random_draws_extreme_has_p_one(monkeypatch):
    random_bits = [[index % 2] * 21 for index in range(5)]
    result, rng = _controlled_monte_carlo(monkeypatch, random_bits)

    assert rng.offset == 5
    assert result["random_extreme_count"] == 5
    assert result["raw_p"] == 1.0


def test_monte_carlo_chance_all_positive_draw_is_not_special_cased(monkeypatch):
    random_bits = _mixed_random_bits(21, 4) + [[1] * 21]
    result, rng = _controlled_monte_carlo(monkeypatch, random_bits)

    assert rng.offset == 5
    assert result["observed_permutation_forced_in_draws"] is False
    assert result["random_extreme_count"] == 1
    assert result["raw_p"] == pytest.approx(2 / 6)


def test_monte_carlo_result_is_independent_of_other_inference_call_order():
    values = {"0.2": [1.0] * 11, "0.4": [0.5] * 10}
    seed = "4" * 64
    before = inference.paired_sign_flip(values, 1000, seed)
    inference.percentile_stratified_bootstrap(
        {"0.2": [0.1, 0.2], "0.4": [0.3, 0.4]}, 25, "5" * 64
    )
    inference.holm_step_down(
        dict(zip(statistics.COMPARATORS, (0.01, 0.04, 0.03, 0.20))),
        list(statistics.COMPARATORS),
    )
    after = inference.paired_sign_flip(values, 1000, seed)
    assert before == after


def test_monte_carlo_known_effect_matches_independent_reference():
    values_by_stratum = {"0.2": [1.0] * 11, "0.4": [0.5] * 10}
    random_draws = 100000
    seed = "6" * 64
    production = inference.paired_sign_flip(
        values_by_stratum, random_draws, seed
    )

    values = []
    weights = []
    for differences in values_by_stratum.values():
        stratum_weight = 1.0 / len(values_by_stratum) / len(differences)
        values.extend(differences)
        weights.extend([stratum_weight] * len(differences))
    weighted = np.abs(np.asarray(values, dtype=np.float64)) * np.asarray(
        weights, dtype=np.float64
    )
    observed = abs(math.fsum(w * value for w, value in zip(weights, values)))
    tolerance = max(1e-15, observed * 1e-14)
    reference_rng = np.random.Generator(np.random.PCG64(int(seed, 16)))
    reference_extreme = 0
    remaining = random_draws
    while remaining:
        count = min(4096, remaining)
        random_bits = reference_rng.integers(
            0, 2, size=(count, len(values)), dtype=np.int8
        )
        signs = np.where(random_bits == 0, -1.0, 1.0)
        reference_statistics = np.abs(signs @ weighted)
        reference_extreme += int(
            np.count_nonzero(reference_statistics >= observed - tolerance)
        )
        remaining -= count

    assert production["observed_statistic"] == observed
    assert production["random_extreme_count"] == reference_extreme
    assert production["p_value_numerator"] == reference_extreme + 1
    assert production["p_value_denominator"] == random_draws + 1
    assert production["raw_p"] == (reference_extreme + 1) / (random_draws + 1)


def test_holm_known_vector_and_stable_display_order():
    order = list(statistics.COMPARATORS)
    raw = dict(zip(order, (0.01, 0.04, 0.03, 0.20)))
    adjusted = inference.holm_step_down(raw, order)
    assert list(adjusted) == order
    assert [adjusted[name]["raw_p"] for name in order] == [raw[name] for name in order]
    assert [adjusted[name]["holm_rank"] for name in order] == [1, 3, 2, 4]
    assert adjusted[order[0]]["holm_adjusted_p"] == pytest.approx(0.04)
    assert adjusted[order[2]]["holm_adjusted_p"] == pytest.approx(0.09)
    assert adjusted[order[1]]["holm_adjusted_p"] == pytest.approx(0.09)
    assert adjusted[order[3]]["holm_adjusted_p"] == pytest.approx(0.20)
    by_rank = sorted(adjusted.values(), key=lambda value: value["holm_rank"])
    assert [value["holm_adjusted_p"] for value in by_rank] == sorted(
        value["holm_adjusted_p"] for value in by_rank
    )
    assert all(0.0 <= value["holm_adjusted_p"] <= 1.0 for value in by_rank)
    assert [adjusted[name]["reject_at_0_05"] for name in order] == [
        True,
        False,
        False,
        False,
    ]


def test_synthetic_known_hp_and_whole_effects_and_pair_counts(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    cases, _ = statistics._annotate(dataset)
    rows = statistics.build_confirmatory_effects(cases, dataset["manifest_sha256"])
    nonblock = rows[0]
    assert nonblock["hp_point_estimate"] == 1.0
    assert nonblock["hp_ci_lower"] == nonblock["hp_ci_upper"] == 1.0
    assert nonblock["whole_point_estimate"] == 0.5
    assert nonblock["randomization_method"] == "exact_sign_flip"
    assert nonblock["random_draws"] is None
    assert nonblock["observed_permutation_forced_in_draws"] is None
    assert nonblock["observed_permutation_accounting"] is None
    assert nonblock["plus_one_correction"] is False
    assert nonblock["raw_p"] == (
        nonblock["p_value_numerator"] / nonblock["p_value_denominator"]
    )
    assert nonblock["asap_block_only"] == 8
    assert nonblock["comparator_only"] == 0
    assert nonblock["both_pass"] == 0
    assert nonblock["neither_pass"] == 8
    assert nonblock["case_pair_count"] == 16


def test_grid_average_and_case_macro_jmr(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    cases, _ = statistics._annotate(dataset)
    rows = statistics.build_algorithm_summary(cases, dataset["manifest_sha256"])
    block = rows[0]
    assert block["hp_pass"] == 1.0
    assert block["whole_pass"] == 0.5
    assert block["overall_jmr"] == pytest.approx(0.0005)
    assert block["completion_ratio"] == pytest.approx((1010 - 0.5) / 1010)


def test_cell_differences_and_mechanism_rates(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    cases, _ = statistics._annotate(dataset)
    cells = statistics.build_cell_summary(cases, dataset["manifest_sha256"], statistics.ALGORITHMS)
    comparison = next(row for row in cells if row["record_type"] == "comparison" and row["comparator"] == "ASAP-NONBLOCK")
    assert comparison["hp_risk_difference"] == 1.0
    mechanisms = statistics.build_mechanism_summary(cases, statistics.ALGORITHMS)
    bypass = next(row for row in mechanisms if row["mechanism"] == "BypassRate" and row["algorithm"] == "ASAP-NONBLOCK")
    undefined = next(row for row in mechanisms if row["mechanism"] == "BypassRate" and row["algorithm"] == "ASAP-BLOCK")
    clipping = next(row for row in mechanisms if row["mechanism"] == "clipping_ratio" and row["algorithm"] == "ASAP-BLOCK")
    assert bypass["macro_mean"] == bypass["median"] == bypass["exposure_pooled_rate"] == 0.5
    assert bypass["positive_numerator_taskset_count"] == 4
    assert bypass["positive_denominator_taskset_count"] == 4
    assert undefined["macro_mean"] is None
    assert undefined["defined_fraction"] == 0.0
    assert undefined["positive_numerator_taskset_count"] == 0
    assert undefined["positive_denominator_taskset_count"] == 0
    assert clipping["macro_mean"] == pytest.approx(0.1)


def test_priority_rank_jmr_uses_task_ratios(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    _, tasks = statistics._annotate(dataset)
    rows = statistics.build_rank_jmr(tasks, statistics.ALGORITHMS)
    block_rank4 = next(row for row in rows if row["algorithm"] == "ASAP-BLOCK" and row["priority_rank"] == 4)
    nonblock_rank0 = next(row for row in rows if row["algorithm"] == "ASAP-NONBLOCK" and row["priority_rank"] == 0)
    assert block_rank4["point_estimate"] == pytest.approx(0.005)
    assert nonblock_rank0["point_estimate"] == pytest.approx(0.01)


def test_pilot_gate_is_incomplete_without_explicit_noninterference_evidence(
    synthetic_root
):
    dataset = statistics.load_analysis(synthetic_root)
    cases, _ = statistics._annotate(dataset)
    cells = statistics.build_cell_summary(
        cases, dataset["manifest_sha256"], statistics.PILOT_ALGORITHMS
    )
    mechanisms = statistics.build_mechanism_summary(
        cases, statistics.PILOT_ALGORITHMS
    )
    gate = statistics.build_pilot_gate(dataset, cells, mechanisms)
    assert gate["status"] == "incomplete"
    assert gate["technical_evidence_present"] is False
    assert gate["checks"]["instrumentation_non_interference_explicit"] is False
    assert gate["contains_ranking_or_significance"] is False
    serialized = statistics.compact_json(gate).lower()
    assert "p_value" not in serialized
    assert "ranking" in serialized  # only the explicit contains_ranking=false declaration
    dataset["manifest"]["technical_evidence"] = {
        "technical_error_count": 0,
        "final_timeout_count": 0,
        "cpu_only_pass": True,
        "unit_identity_audit_pass": True,
        "instrumentation_non_interference_pass": True,
        "evidence_sha256": "e" * 64,
    }
    complete = statistics.build_pilot_gate(dataset, cells, mechanisms)
    assert complete["status"] == "pass"
    assert all(complete["checks"].values())


def test_input_order_does_not_change_aggregated_rows(synthetic_root):
    dataset = statistics.load_analysis(synthetic_root)
    cases, tasks = statistics._annotate(dataset)
    forward = statistics.build_rank_jmr(tasks, statistics.ALGORITHMS)
    reverse = statistics.build_rank_jmr(list(reversed(tasks)), statistics.ALGORITHMS)
    assert forward == reverse
    forward_mechanism = statistics.build_mechanism_summary(cases, statistics.ALGORITHMS)
    reverse_mechanism = statistics.build_mechanism_summary(list(reversed(cases)), statistics.ALGORITHMS)
    assert forward_mechanism == reverse_mechanism
    reordered = dict(dataset)
    reordered["cases"] = list(reversed(dataset["cases"]))
    reordered["tasks"] = list(reversed(dataset["tasks"]))
    assert statistics.analysis_name(dataset) == statistics.analysis_name(reordered)


def _synthetic_clusters(cases):
    clusters = {}
    for row in cases:
        clusters.setdefault(statistics.cluster_key(row), []).append(row)
    return clusters


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_algorithm",
        "duplicate_algorithm",
        "missing_lambda",
        "duplicate_cluster_cell",
        "unknown_utilization",
        "unknown_lambda",
        "wrong_rho",
    ],
)
def test_grid_structure_failures_are_closed(mutation):
    cases, _ = _synthetic_rows()
    if mutation == "missing_algorithm":
        cases.pop(0)
    elif mutation == "duplicate_algorithm":
        cases[0] = dict(cases[0], algorithm="ASAP-NONBLOCK")
    elif mutation == "missing_lambda":
        cases = [row for row in cases if row["lambda_E"] != "0.70"]
    elif mutation == "duplicate_cluster_cell":
        cases.append(copy.deepcopy(cases[0]))
    elif mutation == "unknown_utilization":
        cases[0] = dict(cases[0], utilization="0.9")
    elif mutation == "unknown_lambda":
        cases[0] = dict(cases[0], lambda_E="9.9")
    elif mutation == "wrong_rho":
        cases[0] = dict(cases[0], rho_E="1")
    with pytest.raises(statistics.StatisticsError):
        statistics._validate_grid_structure(
            cases,
            _synthetic_clusters(cases),
            {"0.2", "0.4"},
            {"0.7", "1"},
            {"2"},
            statistics.ALGORITHMS,
            4,
            18,
        )


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("pilot", "i5d_statistics_authorized"),
        ("formal-main", "i5d_statistics_authorized"),
        ("negative-control", "i5d_statistics_authorized"),
    ],
)
def test_current_candidate_v3_does_not_authorize_campaign_statistics(
    synthetic_root, mode, message
):
    dataset = statistics.load_analysis(synthetic_root)
    with pytest.raises(statistics.StatisticsError, match=message):
        statistics._mode_authorization(mode, dataset, dirty=False)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("pilot", "authorize Pilot"),
        ("formal-main", "formal_runs_authorized"),
        ("negative-control", "authorize Negative Control"),
    ],
)
def test_each_campaign_requires_its_explicit_candidate_authorization(
    synthetic_root, mode, message
):
    dataset = statistics.load_analysis(synthetic_root)
    dataset["candidate"] = copy.deepcopy(dataset["candidate"])
    dataset["candidate"]["governance"]["i5d_statistics_authorized"] = True
    with pytest.raises(statistics.StatisticsError, match=message):
        statistics._mode_authorization(mode, dataset, dirty=False)


def test_formal_mode_rejects_not_for_paper_rows_even_with_authorized_identity(
    synthetic_root, monkeypatch
):
    dataset = statistics.load_analysis(synthetic_root)
    dataset["candidate"] = copy.deepcopy(dataset["candidate"])
    dataset["candidate"].update(
        {
            "final_code_commit": "a" * 40,
            "final_git_tag": "v-final",
            "formal_runtime_binary_path": "bin/rtsim",
            "formal_runtime_binary_sha256": "b" * 64,
            "freeze_status": "final_executable",
        }
    )
    dataset["candidate"]["governance"]["formal_runs_authorized"] = True
    dataset["candidate"]["governance"]["i5d_statistics_authorized"] = True
    custom = {
        "phase": "formal_main",
        "case_count": 144,
        "task_count": 1440,
        "pairing_group_count": 16,
        "cluster_count": 8,
        "utilizations": ["0.2", "0.4"],
        "lambdas": ["0.70", "1.00"],
        "rhos": ["2"],
        "clusters_per_utilization": 4,
        "cases_per_cluster": 18,
        "paper_results_authorized": True,
    }
    monkeypatch.setitem(statistics.CONTRACT["mode_contracts"], "formal-main", custom)
    with pytest.raises(statistics.StatisticsError, match="not_for_paper"):
        statistics._mode_authorization("formal-main", dataset, dirty=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("analysis_schema_version", 1, "schema"),
        ("analysis_contract_sha256", "0" * 64, "analysis contract"),
        ("observability_contract_sha256", "0" * 64, "observability contract"),
        ("source_code_commit", None, "source commit"),
        ("extractor_version_sha256", "", "extractor identity"),
    ],
)
def test_manifest_identity_failures_are_closed(synthetic_root, field, value, message):
    path = synthetic_root / "analysis_manifest.json"
    document = json.loads(path.read_text())
    document[field] = value
    path.write_bytes(statistics.pretty_json_bytes(document))
    with pytest.raises(statistics.StatisticsError, match=message):
        statistics.load_analysis(synthetic_root)


def test_analysis_audit_false_is_rejected(synthetic_root):
    audit_path = synthetic_root / "analysis_audit.json"
    audit = json.loads(audit_path.read_text())
    audit["overall_pass"] = False
    audit_bytes = statistics.pretty_json_bytes(audit)
    audit_path.write_bytes(audit_bytes)
    manifest_path = synthetic_root / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_file_sha256"]["analysis_audit.json"] = _sha(audit_bytes)
    manifest_path.write_bytes(statistics.pretty_json_bytes(manifest))
    with pytest.raises(statistics.StatisticsError, match="overall_pass"):
        statistics.load_analysis(synthetic_root)


def test_case_sha_mismatch_is_rejected(synthetic_root):
    path = synthetic_root / "cases.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(statistics.StatisticsError, match="SHA mismatch"):
        statistics.load_analysis(synthetic_root)


def test_task_sha_mismatch_is_rejected(synthetic_root):
    path = synthetic_root / "tasks.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(statistics.StatisticsError, match="SHA mismatch"):
        statistics.load_analysis(synthetic_root)


def test_csv_parity_failure_is_rejected(synthetic_root):
    csv_path = synthetic_root / "cases.csv"
    material = csv_path.read_bytes().replace(b"formal_main", b"pilot", 1)
    csv_path.write_bytes(material)
    manifest_path = synthetic_root / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_file_sha256"]["cases.csv"] = _sha(material)
    manifest_path.write_bytes(statistics.pretty_json_bytes(manifest))
    with pytest.raises(statistics.StatisticsError, match="parity"):
        statistics.load_analysis(synthetic_root)


def test_duplicate_case_primary_key_is_rejected(synthetic_root):
    path = synthetic_root / "cases.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    lines[1] = lines[0]
    material = b"".join(lines)
    path.write_bytes(material)
    manifest_path = synthetic_root / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_file_sha256"]["cases.jsonl"] = _sha(material)
    manifest_path.write_bytes(statistics.pretty_json_bytes(manifest))
    with pytest.raises(statistics.StatisticsError, match="duplicate case"):
        statistics.load_analysis(synthetic_root)


def test_nonfinite_json_is_rejected_before_statistics(synthetic_root):
    path = synthetic_root / "cases.jsonl"
    material = path.read_bytes().replace(b'"E0_j":1.0', b'"E0_j":NaN', 1)
    path.write_bytes(material)
    manifest_path = synthetic_root / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_file_sha256"]["cases.jsonl"] = _sha(material)
    manifest_path.write_bytes(statistics.pretty_json_bytes(manifest))
    with pytest.raises(statistics.StatisticsError, match="non-finite"):
        statistics.load_analysis(synthetic_root)


def test_unknown_case_field_is_rejected(synthetic_root):
    path = synthetic_root / "cases.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["unknown"] = 1
    lines[0] = statistics.compact_json(row)
    material = ("\n".join(lines) + "\n").encode()
    path.write_bytes(material)
    manifest_path = synthetic_root / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_file_sha256"]["cases.jsonl"] = _sha(material)
    manifest_path.write_bytes(statistics.pretty_json_bytes(manifest))
    with pytest.raises(statistics.StatisticsError, match="unknown or reordered"):
        statistics.load_analysis(synthetic_root)


def test_analysis_and_statistics_roots_inside_repo_are_rejected(tmp_path):
    with pytest.raises(statistics.StatisticsError, match="outside repository"):
        statistics.validate_analysis_root(statistics.REPO_ROOT)
    with pytest.raises(statistics.StatisticsError, match="outside repository"):
        statistics.validate_statistics_root(statistics.REPO_ROOT / "forbidden")


def test_nonempty_statistics_root_is_rejected(tmp_path):
    root = tmp_path / "statistics"
    root.mkdir()
    (root / "existing").write_text("owned")
    with pytest.raises(statistics.StatisticsError, match="absent or empty"):
        statistics.validate_statistics_root(root)


def test_mode_output_closure_excludes_forbidden_inference_and_figures():
    pilot = set(statistics._output_modes("pilot"))
    negative = set(statistics._output_modes("negative-control"))
    formal = set(statistics._output_modes("formal-main"))
    assert {"pilot_gate.json", "cell_summary.jsonl", "mechanism_summary.jsonl"} <= pilot
    assert "confirmatory_effects.jsonl" not in pilot
    assert not any(name.startswith("figure") for name in pilot)
    assert "negative_control_summary.csv" in negative
    assert "confirmatory_effects.jsonl" not in negative
    assert not any(name.startswith("figure") for name in negative)
    assert "confirmatory_effects.jsonl" in formal
    assert sum(name.endswith(".pdf") for name in formal) == 5
    assert sum(name.endswith(".png") for name in formal) == 5


def test_formal_main_fails_closed_with_candidate_v3_and_leaves_no_manifest(synthetic_root, tmp_path):
    output = tmp_path / "formal-failure"
    result = subprocess.run(
        [
            sys.executable,
            str(B4_DIR / "run_statistics.py"),
            "--analysis-root", str(synthetic_root),
            "--statistics-root", str(output),
            "--mode", "formal-main",
            "--strict",
        ],
        cwd=statistics.REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert (output / "statistics_audit.json").is_file()
    assert json.loads((output / "statistics_audit.json").read_text())["overall_pass"] is False
    assert not (output / "statistics_manifest.json").exists()
    assert not list(output.glob("figure*.pdf"))
    assert not list(output.glob("table*.csv"))
