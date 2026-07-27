import hashlib
import json
from pathlib import Path

import pytest


B4_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = B4_DIR.parents[1]
CONTRACT_PATH = B4_DIR / "observability_summary_contract_v1.json"

EXPECTED_V1_SHA256 = {
    "experiments/b4_priority_energy/b4_pe_freeze_candidate_v1.json":
        "d5d2e6cfe7751f15227cb93ca66b17d11455cf5dccbcd768aebafb8623822732",
    "experiments/b4_priority_energy/manifest_protocol_v1.json":
        "e00a1fe5ccc4713a9b6b211dde8d6682919d0f599b16424deaf06661c17e148f",
    "experiments/b4_priority_energy/execution_protocol_v1.json":
        "74fd9ed742ad41dbedb66a5e7de2bbc796e746ae2efb207d2d456deed10cdd34",
    "experiments/b4_priority_energy/integration_smoke_protocol_v1.json":
        "4b7e47cd0e89e31540cc30317f91e54a64d45efe63fca05629ee16e5d1aded85",
    "experiments/b4_priority_energy/protocol_resolution_v1.json":
        "941d754e27e1cf599127550561a14a92f46e3605df26dca3c9a97e0325eecd93",
}


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@pytest.fixture(scope="module")
def contract():
    return json.loads(
        CONTRACT_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
    )


def _names(contract, block):
    return [field["name"] for field in contract[block]]


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_contract_identity_and_activation(contract):
    assert contract["contract_version"] == 1
    assert contract["trace_schema_version"] == 3
    assert contract["default_trace_schema_version"] == 2
    assert contract["activation"]["default_summary_enabled"] is False
    assert contract["activation"]["formal_execution_enabled_by_this_contract"] is False


def test_contract_json_has_no_duplicate_keys():
    json.loads(
        CONTRACT_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
    )


def test_top_level_and_block_field_order(contract):
    assert _names(contract, "top_level_fields") == [
        "observability_summary_contract_version",
        "observability_summary_horizon_ms",
        "mechanism_summary",
        "energy_summary",
        "per_task_summary",
    ]
    assert _names(contract, "mechanism_summary_fields") == [
        "bypass_opportunity_ticks",
        "actual_bypass_ticks",
        "low_priority_bypass_core_ticks",
        "hp_dispatch_demand_ticks",
        "hp_energy_blocked_ticks",
        "hp_energy_blocked_job_ticks",
        "observed_decision_ticks",
    ]
    assert _names(contract, "energy_summary_fields") == [
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
    assert _names(contract, "per_task_summary_fields") == [
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
    ]
    assert contract["ordering"]["top_level_additions_insert_before"] == (
        "simulation_completion_reason"
    )


def test_field_types_and_units_are_frozen(contract):
    for block in (
        "top_level_fields",
        "mechanism_summary_fields",
        "energy_summary_fields",
        "per_task_summary_fields",
    ):
        for field in contract[block]:
            assert field["json_type"] in {
                "integer", "number", "string", "boolean", "object", "array"
            }
            assert "unit" in field
    energy = contract["energy_summary_fields"]
    assert all(field["unit"] == "J" for field in energy[:7])
    assert all(field["json_type"] == "number" for field in energy[:7])
    assert all(field["json_type"] == "integer" for field in energy[7:])


def test_exact_ten_task_top4_bottom6_contract(contract):
    invariants = contract["invariants"]
    assert invariants["task_count"] == 10
    assert invariants["priority_rank_coverage"] == "0..9"
    assert invariants["top4_count"] == 4
    assert invariants["bottom6_count"] == 6
    assert invariants["top4_predicate"] == "priority_rank < 4"
    assert invariants["bottom6_predicate"] == "priority_rank >= 4"
    is_top4 = contract["per_task_summary_fields"][2]
    is_bottom6 = contract["per_task_summary_fields"][3]
    assert is_top4["derived_from"] == "priority_rank < 4"
    assert is_bottom6["derived_from"] == "priority_rank >= 4"


def test_horizon_contract_is_schema3_v1_strict(contract):
    horizon = contract["horizon_contract"]
    assert horizon["summary_horizon_equals_trace_duration"] is True
    assert horizon["release_window_requires_summary_horizon_equals_observation_horizon"] is True
    assert horizon["generation_horizon_is_independent"] is True
    assert horizon["generation_horizon_must_be_less_than_observation_horizon"] is True
    assert horizon["summary_horizon_less_than_observation_horizon_forbidden"] is True


def test_binary64_has_one_max_digits10_json_number_representation(contract):
    numeric = contract["numeric_contract"]
    assert numeric["energy_unit"] == "J"
    assert numeric["authoritative_representation"] == "json_number"
    assert numeric["double_serialization"] == "max_digits10"
    assert numeric["exact_string_copy"] is False
    assert numeric["non_finite_values_forbidden"] is True
    assert numeric["tolerance"] == {
        "coefficient": 1e-9,
        "formula": "1e-9 * max(1, abs(a), abs(b))",
    }
    serialized_field_names = []
    for block in (
        "top_level_fields",
        "mechanism_summary_fields",
        "energy_summary_fields",
        "per_task_summary_fields",
    ):
        serialized_field_names.extend(_names(contract, block))
    assert not any(name.endswith("_exact") for name in serialized_field_names)


def test_v1_candidate_and_protocol_bytes_are_unchanged():
    for relative_path, expected_sha in EXPECTED_V1_SHA256.items():
        material = (REPO_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(material).hexdigest() == expected_sha


def test_contract_canonical_reread_is_stable():
    first = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    first_canonical = json.dumps(
        first, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    second = json.loads(first_canonical.decode("utf-8"))
    second_canonical = json.dumps(
        second, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert first_canonical == second_canonical
