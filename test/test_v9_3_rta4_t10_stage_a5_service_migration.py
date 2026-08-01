from __future__ import annotations

from fractions import Fraction
import inspect
import json
from pathlib import Path

from experiments.v9_3.rta4_t10_service_migration_audit import (
    EXACT_SERVICE_MODEL,
    LEGACY_SERVICE_MODEL,
    exact_linear_service_prefix,
)


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "artifacts/audit/rta4_t10_stage_a5_service_contract_migration.json"
STAGE_A_AUDIT = ROOT / "artifacts/audit/rta4_t10_stage_a_parity_audit.json"


def test_exact_linear_service_is_direct_fraction_length_over_ten():
    prefix = exact_linear_service_prefix(34)
    assert len(prefix) == 34
    assert prefix == tuple(Fraction(length, 10) for length in range(34))
    assert prefix[0] == 0
    assert prefix[1] == Fraction(1, 10)
    assert prefix[-1] == Fraction(33, 10)


def test_exact_linear_service_constructor_has_no_float_conversion_path():
    source = inspect.getsource(exact_linear_service_prefix)
    for forbidden in (
        "float(", "Fraction.from_float", "Decimal.from_float",
    ):
        assert forbidden not in source


def test_stage_a_authorization_remains_false_and_unchanged():
    stage_a = json.loads(STAGE_A_AUDIT.read_text(encoding="utf-8"))
    assert stage_a["schema"] == "ASAP_BLOCK_RTA4_T10_STAGE_A_PARITY_AUDIT_V1"
    assert stage_a["stage_b_authorized"] is False


def test_complete_stage_a5_audit_authorizes_only_infrastructure():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert audit["service_contracts"]["legacy"]["service_model"] == (
        LEGACY_SERVICE_MODEL
    )
    assert audit["service_contracts"]["exact"]["service_model"] == (
        EXACT_SERVICE_MODEL
    )
    assert audit["service_contracts"]["exact"]["implementation"] == (
        "Fraction(length, 10)"
    )
    assert audit["normalized_taskset_count"] == 176
    assert audit["cell_comparison_count"] == 352
    assert audit["method_comparison_count"] == 1408
    assert audit["task_result_record_count"] == 14080
    assert audit["exact_adapter_parity_mismatch_count"] == 0
    assert audit["exact_input_parity_mismatch_count"] == 0
    assert audit["exact_float_decision_path_count"] == 0
    assert audit["exact_input_identity_change_count"] == 1408
    assert audit["input_identity_failure_count"] == 0
    assert audit["script_failure_count"] == 0
    assert audit["unclassified_internal_error_count"] == 0
    assert audit["dominance_violation_count"] == 0
    assert audit["stage_b_infrastructure_authorized"] is True
    assert audit["formal_t10_campaign_authorized"] is False
