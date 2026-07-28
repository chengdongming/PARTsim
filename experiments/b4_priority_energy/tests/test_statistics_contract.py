import hashlib
import json
from pathlib import Path

import pytest


B4_DIR = Path(__file__).resolve().parents[1]
CONTRACT_PATH = B4_DIR / "statistics_contract_v1.json"


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result
        result[key] = value
    return result


@pytest.fixture(scope="module")
def contract():
    return json.loads(
        CONTRACT_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicates,
    )


def test_contract_has_no_duplicate_keys_and_is_v1(contract):
    assert contract["contract_version"] == 1
    assert contract["contract_name"] == "B4-PE-I5D-deterministic-statistics-v1"


def test_required_input_contract_identities_are_exact(contract):
    assert contract["required_analysis_contract_sha256"] == "25d0cfff0fba81979d15b5b70df842fc2e84f969574fa4cd73fc7ad2527c9318"
    assert contract["required_observability_contract_sha256"] == "4e982f5a58a26507c9ab1b1b8d0b732e651d4657f10cf16744d3278d11186efe"


def test_algorithm_order_is_frozen(contract):
    assert contract["algorithm_order"] == [
        "ASAP-BLOCK", "ASAP-NONBLOCK", "ASAP-SYNC",
        "ALAP-BLOCK", "ALAP-NONBLOCK", "ALAP-SYNC",
        "ST-BLOCK", "ST-NONBLOCK", "ST-SYNC",
    ]


def test_comparison_order_is_frozen(contract):
    assert contract["comparison_order"] == [
        "ASAP-NONBLOCK", "ASAP-SYNC", "ALAP-BLOCK", "ST-BLOCK"
    ]


def test_inference_counts_and_seed_are_frozen(contract):
    inference = contract["inference"]
    assert inference["bootstrap_replicates"] == 10000
    assert inference["randomization_random_draws"] == 100000
    assert inference["root_seed"] == 20260728
    assert inference["exact_sign_flip_max_nonzero"] == 20
    assert inference["exact_sign_flip_plus_one_correction"] is False
    assert (
        inference["randomization_observed_permutation_forced_in_draws"] is False
    )
    assert (
        inference["randomization_observed_permutation_accounting"]
        == "plus_one_only"
    )
    assert inference["randomization_plus_one_correction"] is True
    assert (
        inference["randomization_p_value"]
        == "(random_extreme_count + 1) / (random_draws + 1)"
    )
    assert "randomization_replicates" not in inference
    assert "randomization_observed_permutation_included" not in inference
    assert inference["rng"] == "NumPy PCG64 seeded by the full derived SHA-256 integer"
    assert inference["percentile_quantile_method"] == "linear"
    assert "statistics_contract_sha256" in inference["seed_material"]


@pytest.mark.parametrize(
    ("mode", "cases", "tasks", "pairing", "clusters"),
    [
        ("pilot", 2400, 24000, 480, 60),
        ("formal-main", 18000, 180000, 2000, 500),
        ("negative-control", 5400, 54000, 600, 300),
    ],
)
def test_mode_contract_counts(contract, mode, cases, tasks, pairing, clusters):
    value = contract["mode_contracts"][mode]
    assert (value["case_count"], value["task_count"]) == (cases, tasks)
    assert (value["pairing_group_count"], value["cluster_count"]) == (pairing, clusters)


def test_validation_governance_is_not_for_paper(contract):
    validation = contract["mode_contracts"]["validation"]
    assert validation["paper_results_authorized"] is False
    assert validation["watermark"] == "VALIDATION ONLY — NOT FOR PAPER"


def test_mode_grids_match_frozen_experiment_matrix(contract):
    pilot = contract["mode_contracts"]["pilot"]
    formal = contract["mode_contracts"]["formal-main"]
    negative = contract["mode_contracts"]["negative-control"]
    assert pilot["utilizations"] == ["0.3", "0.4", "0.5"]
    assert pilot["lambdas"] == ["0.70", "0.85", "1.00", "1.15"]
    assert pilot["rhos"] == ["1", "2"]
    assert formal["utilizations"] == ["0.2", "0.3", "0.4", "0.5", "0.6"]
    assert formal["rhos"] == ["2"]
    assert negative["utilizations"] == ["0.3", "0.4", "0.5"]
    assert negative["lambdas"] == ["0.85", "1.00"]
    assert negative["rhos"] == ["1"]


def test_na_contract_never_maps_zero_denominator_to_zero(contract):
    ratio = contract["ratio_contract"]
    assert ratio["zero_denominator_json"] is None
    assert ratio["zero_denominator_csv"] == ""


def test_cluster_fields_exclude_lambda_source_algorithm_and_pairing(contract):
    fields = set(contract["cluster_dimension_order"])
    excluded = set(contract["cluster_contract"]["excluded_fields"])
    assert not fields & excluded
    assert {"phase", "rho_E", "utilization", "taskset_id", "taskset_semantic_hash"} <= fields


def test_pilot_cluster_normalizes_the_complete_rho_set(contract):
    assert contract["cluster_contract"]["pilot_rho_normalization"] == ["1", "2"]
    assert contract["mode_contracts"]["pilot"]["cases_per_cluster"] == 40


def test_output_order_is_complete_and_unique(contract):
    outputs = contract["output_order"]
    assert len(outputs) == 29
    assert len(outputs) == len(set(outputs))
    assert outputs[-2:] == ["statistics_audit.json", "statistics_manifest.json"]


def test_row_fields_are_nonempty_and_unique(contract):
    for fields in contract["row_field_order"].values():
        assert fields
        assert len(fields) == len(set(fields))


def test_hash_dag_is_one_way(contract):
    assert contract["hash_dag"] == [
        "numeric_table_figure_outputs",
        "statistics_audit.json",
        "statistics_manifest.json",
    ]


def test_figure_and_table_rendering_contract_is_frozen(contract):
    figures = contract["figure_contract"]
    assert figures["backend"] == "Agg"
    assert figures["font_family"] == "DejaVu Sans"
    assert figures["png_dpi"] == 300
    assert figures["figure_sizes_inches"]["figure1"] == [8.0, 4.5]
    assert figures["figure_sizes_inches"]["figure5"] == [11.0, 14.0]
    assert figures["pdf_metadata"]["CreationDate"] is None
    assert figures["pdf_metadata"]["ModDate"] is None
    assert figures["marker_order"] == ["o", "s", "^", "D", "v", "P", "X", "<", ">"]
    tables = contract["table_contract"]
    assert tables["percent_decimals"] == 2
    assert tables["p_value_significant_digits"] == 6


def test_governance_forbids_campaign_execution_and_claims(contract):
    governance = contract["governance"]
    assert governance["event_array_forbidden"] is True
    assert governance["jsonl_authoritative"] is True
    assert governance["performance_claims_forbidden"] is True
    assert governance["failed_run_has_no_success_manifest"] is True


def test_contract_bytes_have_lf_and_stable_sha_shape():
    material = CONTRACT_PATH.read_bytes()
    assert material.endswith(b"\n")
    assert b"\r\n" not in material
    assert len(hashlib.sha256(material).hexdigest()) == 64
