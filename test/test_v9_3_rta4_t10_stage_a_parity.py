from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path

from experiments.v9_3.rta4_formal_config_v3 import (
    formal_taskset_store_identity_v3,
    load_rta4_campaign_v3,
)
from experiments.v9_3.rta4_formal_plan_v3 import describe_formal_plan_v3
from experiments.v9_3.rta4_t10_parity_audit import (
    BACKGROUND_TASKS,
    _frozen_beta,
    normalize_t10_record,
)


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "artifacts/audit/rta4_t10_stage_a_parity_audit.json"
E1 = ROOT / "configs/v9_3_rta4_e1_critical_e0_v1.yaml"


def _seven_task_source() -> dict:
    return {
        "taskset_index": 0,
        "original_taskset_index": 1,
        "seed": 1918273646,
        "tasks": [
            {"name": "tau_1", "C": 1, "D": 2, "T": 3, "power": "1/10"},
            {"name": "tau_2", "C": 1, "D": 4, "T": 5, "power": "1/5"},
            {"name": "tau_3", "C": 1, "D": 6, "T": 7, "power": "1/4"},
            {"name": "tau_4", "C": 1, "D": 8, "T": 11, "power": "1/50"},
            {"name": "tau_5", "C": 1, "D": 12, "T": 13, "power": "1/80"},
            {"name": "tau_6", "C": 2, "D": 16, "T": 17, "power": "1/20"},
            {"name": "tau_7", "C": 1, "D": 20, "T": 23, "power": "1/6"},
        ],
    }


def test_frozen_t10_normalization_appends_exact_background_contract():
    normalized = normalize_t10_record(_seven_task_source())
    assert normalized["task_count"] == 10
    assert normalized["mechanism_core_task_count"] == 7
    assert normalized["tasks"][7:] == list(BACKGROUND_TASKS)
    assert normalized["task_order"] == [f"tau_{index}" for index in range(1, 11)]
    assert sum(
        (Fraction(row["C"], row["T"]) for row in normalized["tasks"][7:]),
        Fraction(),
    ) == Fraction(1, 12)


def test_frozen_configured_rate_is_not_exact_linear_beta():
    beta = _frozen_beta(34)
    assert beta[0] == 0
    assert beta[1] == Fraction.from_float(float(Fraction(1, 10)))
    assert beta[1] != Fraction(1, 10)


def test_complete_audit_records_zero_parity_mismatch_and_closed_b_gate():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert audit["cell_comparison_count"] == 352
    assert audit["method_comparison_count"] == 1408
    assert audit["frozen_evidence_mismatch_count"] == 0
    assert audit["parity_mismatch_count"] == 0
    assert audit["input_semantic_mismatch_count"] == 0
    assert audit["dominance_violation_count"] == 0
    assert audit["acceptance_counts"] == {
        "21/40": {"CW": 10, "LOC": 29, "PH": 125, "SEQ": 131},
        "11/20": {"CW": 25, "LOC": 54, "PH": 143, "SEQ": 152},
    }
    assert audit["service_contract"]["exact_linear_match"] is False
    assert audit["stage_b_authorized"] is False


def test_v3_e1_scientific_plan_and_store_identities_remain_frozen():
    campaign = load_rta4_campaign_v3(E1)
    plan = describe_formal_plan_v3(campaign.normalized_scientific_config)
    assert campaign.raw_campaign_file_sha256 == (
        "f0632b46b405afd576b815c34b99b87bb2766ee19c3ef3b5f951413f90c3420b"
    )
    assert campaign.normalized_scientific_config_sha256 == (
        "d5762c90ea9df3e386360c2448039ea6e39c70f4ebfc0372d2a729b6ba915638"
    )
    assert plan["plan_sha256"] == (
        "81231be0dce9693afbf72111493c0fe25500bd8792cbddac9b8ac99796d4f46f"
    )
    assert formal_taskset_store_identity_v3(campaign.normalized_scientific_config) == (
        "cc43d5b55c6d4157a1270d9c659e925aa82d7d1e88b60dead6a7274d9658606d"
    )
