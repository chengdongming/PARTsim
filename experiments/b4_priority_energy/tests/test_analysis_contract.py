import json
from pathlib import Path


B4_DIR = Path(__file__).resolve().parents[1]
CONTRACT_PATH = B4_DIR / "analysis_contract_v1.json"


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_contract():
    return json.loads(
        CONTRACT_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
    )


def test_contract_json_has_no_duplicate_keys():
    _load_contract()


def test_contract_identity_and_required_sections():
    contract = _load_contract()
    assert contract["contract_name"] == (
        "B4-PE-deterministic-analysis-extraction-v1"
    )
    assert contract["contract_version"] == 1
    assert contract["analysis_schema_version"] == 1
    assert list(contract) == [
        "contract_name",
        "contract_version",
        "analysis_schema_version",
        "accepted_trace_schemas",
        "authoritative_outputs",
        "convenience_outputs",
        "metadata_outputs",
        "case_primary_key",
        "task_primary_key",
        "case_identity_field_order",
        "case_field_order",
        "task_field_order",
        "mechanism_field_order",
        "energy_field_order",
        "task_metric_field_order",
        "pairing_contract",
        "pass_contract",
        "ordering_contract",
        "numeric_contract",
        "determinism_contract",
        "audit_contract",
        "governance",
    ]


def test_contract_output_and_primary_keys_are_frozen():
    contract = _load_contract()
    assert contract["authoritative_outputs"] == ["cases.jsonl", "tasks.jsonl"]
    assert contract["convenience_outputs"] == ["cases.csv", "tasks.csv"]
    assert contract["case_primary_key"] == ["case_id"]
    assert contract["task_primary_key"] == ["case_id", "priority_rank"]


def test_case_and_task_field_order_is_frozen():
    contract = _load_contract()
    identity = contract["case_identity_field_order"]
    case_fields = contract["case_field_order"]
    task_fields = contract["task_field_order"]
    assert case_fields[: len(identity)] == identity
    assert task_fields[: len(identity)] == identity
    assert case_fields[-1] == "lp_completed_response_time_max_ms"
    assert task_fields[-14:] == [
        "task_name",
        "priority_rank",
        "is_top4",
        "is_bottom6",
        "released_jobs",
        "completed_jobs",
        "terminated_jobs",
        "deadline_miss_jobs",
        "unfinished_at_horizon_jobs",
        "executed_core_ticks",
        "completed_response_time_count",
        "completed_response_time_sum_ms",
        "completed_response_time_max_ms",
        "task_pass",
    ]
    assert len(case_fields) == len(set(case_fields))
    assert len(task_fields) == len(set(task_fields))


def test_mechanism_energy_and_task_metric_order_is_complete():
    contract = _load_contract()
    assert contract["mechanism_field_order"] == [
        "bypass_opportunity_ticks",
        "actual_bypass_ticks",
        "low_priority_bypass_core_ticks",
        "hp_dispatch_demand_ticks",
        "hp_energy_blocked_ticks",
        "hp_energy_blocked_job_ticks",
        "observed_decision_ticks",
    ]
    assert contract["energy_field_order"] == [
        "offered_energy_j",
        "credited_energy_j",
        "clipped_energy_j",
        "consumed_energy_j",
        "battery_min_j",
        "battery_max_j",
        "battery_final_j",
        "battery_empty_ticks",
        "battery_full_ticks",
        "observed_energy_intervals",
    ]
    assert len(contract["task_metric_field_order"]) == 9


def test_pairing_and_algorithm_order_are_frozen():
    contract = _load_contract()
    pairing = contract["pairing_contract"]
    assert pairing["schema3_group_size"] == 9
    assert pairing["key_derivation"].startswith("lowercase SHA-256")
    assert "configured_scheduler" in pairing["algorithm_identity_excluded"]
    assert "phase" in pairing["pairing_dimension_order"]
    assert "taskset_id" in pairing["pairing_dimension_order"]
    assert "source_identity" in pairing["pairing_dimension_order"]
    assert "E0_j" in pairing["pairing_dimension_order"]
    assert contract["ordering_contract"]["algorithm_canonical_order"] == [
        "ASAP-BLOCK",
        "ASAP-NONBLOCK",
        "ASAP-SYNC",
        "ALAP-BLOCK",
        "ALAP-NONBLOCK",
        "ALAP-SYNC",
        "ST-BLOCK",
        "ST-NONBLOCK",
        "ST-SYNC",
    ]


def test_pass_numeric_determinism_and_governance_are_fail_closed():
    contract = _load_contract()
    assert contract["pass_contract"]["event_array_is_not_an_analysis_source"] is True
    assert contract["numeric_contract"]["csv_float_significant_digits"] == 17
    assert contract["numeric_contract"]["non_finite_values_forbidden"] is True
    assert contract["determinism_contract"][
        "identical_inputs_require_byte_identical_outputs"
    ] is True
    assert contract["audit_contract"]["requires_strict_input_audit"] is True
    assert contract["governance"]["candidate_v2_mutation_forbidden"] is True
    assert contract["governance"]["no_paper_data_generated"] is True
