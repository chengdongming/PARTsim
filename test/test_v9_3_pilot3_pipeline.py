from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from scripts import analyze_v9_3_pilot3 as analysis
from scripts import run_v9_3_pilot3 as pilot3


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config():
    return pilot3.load_config(PROJECT_ROOT / "configs" / "v9_3_pilot3.yaml")


@pytest.fixture(scope="module")
def intervals(config):
    return pilot3.reconstruct_intervals(config)


@pytest.fixture(scope="module")
def selection(config, intervals):
    return pilot3.rank_and_select_e0(intervals, config)


def test_reconstructs_all_exact_half_open_intervals(intervals):
    assert len(intervals) == 3675
    assert all(isinstance(row["_lower"], Fraction) for row in intervals)
    assert all(row["_lower"] < row["_upper"] for row in intervals)
    assert {row["relation"] for row in intervals} == {
        "DEADLINE_CARRY_IN", "FIXED_CW_CARRY_IN"
    }


def test_exact_candidate_ranking_selects_five_data_values_and_controls(config, selection):
    candidates, selected = selection
    assert len(candidates) > 3675
    assert len(selected) == 7
    assert sum(row["selection_role"] == "DATA" for row in selected) == 5
    assert {row["_value"] for row in selected if row["selection_role"] == "CONTROL"} == {
        Fraction(0), Fraction(1)
    }
    data = [row["_value"] for row in selected if row["selection_role"] == "DATA"]
    minimum = Fraction(config["e0_selection"]["minimum_exact_separation"])
    assert all(abs(left - right) >= minimum for index, left in enumerate(data) for right in data[index + 1:])
    assert all(row["covered_intervals"] > 0 for row in selected if row["selection_role"] == "DATA")


def test_candidate_coverage_uses_inclusive_lower_exclusive_upper():
    intervals = [{
        "_lower": Fraction(1, 10), "_upper": Fraction(1, 5),
        "taskset_id": "t", "task_id": "0", "relation": "r", "U_norm": "0.4",
    }]
    assert pilot3._coverage(Fraction(1, 10), intervals)["covered_intervals"] == 1
    assert pilot3._coverage(Fraction(1, 5), intervals)["covered_intervals"] == 0


def test_paired_seed_has_no_e0_component_and_is_cell_separated():
    values = {
        pilot3.derive_paired_seed(930312, u_index, taskset_index)
        for u_index in range(2) for taskset_index in range(5)
    }
    assert len(values) == 10
    assert pilot3.derive_paired_seed(930312, 1, 4) == pilot3.derive_paired_seed(930312, 1, 4)
    assert pilot3.derive_paired_seed(930312, 1, 4) != pilot3.derive_paired_seed(930412, 1, 4)


def test_replacing_e0_preserves_paired_task_payload(config):
    context = pilot3._pilot2_context(config)
    generated = next(iter(context["generated"].values()))
    left = replace(generated, e0=Fraction(7, 100), e0_index=1)
    right = replace(generated, e0=Fraction(9, 100), e0_index=2)
    assert left.seed == right.seed
    assert left.taskset_id == right.taskset_id
    assert left.task_payload == right.task_payload
    assert left.semantic_hash == right.semantic_hash
    assert left.e0 != right.e0


@pytest.mark.parametrize(
    "metrics, expected",
    [
        ({}, "A_NO_ENVELOPE_STRICTNESS"),
        ({"envelope_strict_accesses": 1}, "B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE"),
        ({"envelope_strict_accesses": 1, "local_only_closures": 1}, "C_LOCAL_ONLY_CLOSURE_CANDIDATE_EQUAL"),
        ({"response_strict_tasks": 1}, "D_RESPONSE_STRICT"),
        ({"response_strict_tasks": 1, "certification_gain_tasksets": 1}, "E_CERTIFICATION_GAIN"),
    ],
)
def test_predeclared_cell_classification(metrics, expected):
    assert analysis.classify_cell(metrics) == expected


def test_adaptive_gate_requires_closure_then_strict_outcome():
    assert pilot3.adaptive_gate_decision("INITIAL_U04_TWO", 0, 0, 0) == (
        "STOP_E0_NO_LOCAL_ONLY_CLOSURE"
    )
    assert pilot3.adaptive_gate_decision("INITIAL_U04_TWO", 1, 0, 0) == (
        "EXPAND_U04_TO_FIVE"
    )
    assert pilot3.adaptive_gate_decision("FULL_U04_FIVE", 2, 0, 0) == (
        "STOP_E0_NO_STRICT_RESPONSE_OR_CERTIFICATION_GAIN"
    )
    assert pilot3.adaptive_gate_decision("FULL_U04_FIVE", 2, 1, 0) == "EXPAND_TO_U06"
    assert pilot3.adaptive_gate_decision("FULL_U04_FIVE", 2, 0, 1) == "EXPAND_TO_U06"


def test_exact_noninteger_e0_reaches_real_production_dispatch(config, selection):
    context = pilot3._pilot2_context(config)
    generated = sorted(context["generated"].values(), key=lambda item: item.taskset_id)[0]
    e0 = next(row["_value"] for row in selection[1] if row["selection_role"] == "DATA")
    generated = replace(generated, e0=e0, e0_index=1)
    execution = pilot3._run_request(
        generated, pilot3.taskset.AnalysisVariant.CW_D, 2, context,
        "pilot3-exact-noninteger-probe",
    )
    assert execution.result is not None
    assert execution.result.solver_status.value not in {"NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE"}
    assert execution.result.dependency_context.e0_canonical_identity == pilot3.pilot1._domain_hash(
        "ASAP_BLOCK:PILOT:E0:v9.3", e0
    )
