from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path
import struct
import subprocess

import pytest
import yaml

import asap_block_rta as legacy_rta
import asap_block_rta_v9_3 as core
import asap_block_rta_v9_3_taskset as taskset
from experiments.v9_3 import exact_energy
from experiments.v9_3.cell_model import derive_seed, expand_cells
from experiments.v9_3.config import ConfigError, load_config, validate_config
from experiments.v9_3.result_writer import (
    ResultWriter,
    ResultWriterError,
    atomic_write_json,
)
from experiments.v9_3.taskset_store import prepare_service_curve


ROOT = Path(__file__).resolve().parents[1]


def _bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", value))[0]


def _neighbor(value: float, direction: int) -> float:
    """Return the adjacent positive binary64 value on older Python runtimes."""

    return struct.unpack(">d", struct.pack(">Q", _bits(value) + direction))[0]


@pytest.mark.parametrize(
    "value",
    [
        float("0.1"),
        float("0.2"),
        float("0.3"),
        float("0.1") * float("0.2"),
        float("0.333333333333333333333333333333"),
    ],
)
def test_binary64_materialization_is_exact_not_decimal_recovery(value):
    demand = exact_energy.materialize_demand_upper_bound(value, "demand")
    supply = exact_energy.materialize_supply_lower_bound(value, "supply")
    expected = Fraction.from_float(value)
    assert demand.exact_value == expected
    assert supply.exact_value == expected
    assert demand.binary64_hex == supply.binary64_hex == value.hex()
    assert demand.direction is exact_energy.EnergyDirection.ENERGY_DEMAND_UPPER_BOUND
    assert supply.direction is exact_energy.EnergyDirection.ENERGY_SUPPLY_LOWER_BOUND
    if value in {0.1, 0.2, 0.3}:
        assert expected != Fraction(str(value))


def test_repeated_supply_accumulation_matches_binary64_left_fold():
    tick = float("0.1") * float("0.2")
    exact_tick = exact_energy.materialize_supply_lower_bound(
        tick, "tick",
    ).exact_value
    trace = (exact_tick,) * 17
    beta = exact_energy.service_curve_lower_bound(trace, len(trace))
    materialized_total = 0.0
    for _ in range(17):
        materialized_total += tick
    assert beta[0] == 0
    assert beta[-1] == Fraction.from_float(materialized_total)
    assert beta[-1] != exact_tick * 17
    assert beta[-1] != Fraction(str(materialized_total))


def test_e0_is_exact_rational_lower_bound_and_rejects_float():
    assert exact_energy.exact_e0_lower_bound("1/3") == Fraction(1, 3)
    assert exact_energy.exact_e0_lower_bound(Decimal("0.1")) == Fraction(1, 10)
    with pytest.raises(exact_energy.ExactEnergyError, match="binary float"):
        exact_energy.exact_e0_lower_bound(0.1)


def test_numeric_contract_is_derived_metadata_not_normalized_config():
    raw = yaml.safe_load(
        (ROOT / "configs/v9_3_core1_smoke.yaml").read_text(encoding="utf-8")
    )
    before = deepcopy(raw)
    normalized = validate_config(raw, expected_core="CORE-1")
    assert raw == before
    assert "numeric_contract" not in normalized

    injected = deepcopy(raw)
    injected["numeric_contract"] = exact_energy.numeric_contract_metadata()
    with pytest.raises(ConfigError, match="derived runtime metadata"):
        validate_config(injected, expected_core="CORE-1")


def test_directional_contract_never_changes_the_materialized_value():
    base = 0.1
    lower = _neighbor(base, -1)
    upper = _neighbor(base, 1)
    assert exact_energy.materialize_demand_upper_bound(
        upper, "demand",
    ).exact_value >= Fraction.from_float(base)
    assert exact_energy.materialize_supply_lower_bound(
        lower, "supply",
    ).exact_value <= Fraction.from_float(base)
    assert exact_energy.exact_e0_lower_bound("1/10") <= Fraction(1, 10)


def _single_task_closure(demand: Fraction, available: Fraction):
    target = core.V93Task("k", 1, 1, 2, demand)
    return core.canonical_closure_search_v9_3(
        core.EnvelopeKind.COMPLETE,
        target,
        (),
        (),
        1,
        {},
        available,
        (Fraction(0),),
    )


def _runtime_start_offset_ms(day_of_year: int, time_of_day_ms: int) -> int:
    """Independent reference for EnergyConfig's production conversion."""

    start_offset_minutes = (
        (day_of_year - 1) * 1440 + int(time_of_day_ms / 60000)
    )
    return int(start_offset_minutes * 60 * 1000)


def _runtime_synthetic_harvest(
    config: legacy_rta.RTASystemConfig, tick: int,
) -> float:
    absolute_time_ms = (
        _runtime_start_offset_ms(config.day_of_year, config.time_of_day_ms)
        + tick
    )
    peak_irradiance = config.base_harvesting_rate / (
        config.pv_area_m2 * config.pv_efficiency
    )
    irradiance = peak_irradiance * legacy_rta._time_factor(absolute_time_ms)
    return (
        irradiance
        * config.pv_area_m2
        * config.pv_efficiency
        * (1.0 * legacy_rta.TICK_SECONDS)
    )


@pytest.mark.parametrize(
    "time_of_day_ms",
    [
        6 * 3600000,
        6 * 3600000 + 1,
        6 * 3600000 + 30000,
        6 * 3600000 + 59999,
        6 * 3600000 + 59998,
        7 * 3600000 - 1,
        21975000,
    ],
    ids=(
        "minute-aligned",
        "minute-plus-1ms",
        "minute-plus-30s",
        "minute-plus-59999ms",
        "near-minute",
        "near-hour",
        "formal-21975000",
    ),
)
def test_harvest_materializer_matches_runtime_minute_phase_across_boundary(
    time_of_day_ms,
):
    system = legacy_rta.load_system_config(
        str(ROOT / "system_config_unified_template.yml")
    )
    system = replace(
        system,
        use_real_solar_data=False,
        day_of_year=187,
        time_of_day_ms=time_of_day_ms,
    )
    trace = legacy_rta._harvest_trace_from_config(system, 60002)
    compared_ticks = (1, 2, 59998, 59999, 60000, 60001, 60002)
    assert [
        _bits(trace[tick - 1]) for tick in compared_ticks
    ] == [
        _bits(_runtime_synthetic_harvest(system, tick))
        for tick in compared_ticks
    ]


def test_formal_phase_counterexample_is_closed_by_runtime_aligned_trace():
    # Frozen witness from the independently compiled Release probe at the old
    # PR head.  Retaining it makes the original failure-to-success flip
    # reproducible without treating either decimal rendering as authoritative.
    old_rta_supply = Fraction.from_float(
        float.fromhex("0x1.2dfd9e1397380p-20")
    )
    runtime_supply = Fraction.from_float(
        float.fromhex("0x1.21e93db45be9fp-20")
    )
    boundary_demand = (old_rta_supply + runtime_supply) / 2
    assert _single_task_closure(
        boundary_demand, old_rta_supply,
    ).solver_status is core.V93SolverStatus.CANDIDATE
    assert _single_task_closure(
        boundary_demand, runtime_supply,
    ).solver_status is core.V93SolverStatus.NO_CANDIDATE

    system = legacy_rta.load_system_config(
        str(ROOT / "system_config_unified_template.yml")
    )
    system = replace(
        system,
        use_real_solar_data=False,
        day_of_year=1,
        time_of_day_ms=21975000,
    )
    repaired_supply = Fraction.from_float(
        legacy_rta._harvest_trace_from_config(system, 1)[0]
    )
    assert repaired_supply == runtime_supply
    assert _single_task_closure(
        boundary_demand, repaired_supply,
    ).solver_status is core.V93SolverStatus.NO_CANDIDATE


def test_closure_boundary_cannot_flip_failure_to_success():
    exact = Fraction.from_float(0.1)
    below = Fraction.from_float(_neighbor(0.1, -1))
    above = Fraction.from_float(_neighbor(0.1, 1))

    assert _single_task_closure(exact, exact).solver_status is core.V93SolverStatus.CANDIDATE
    assert _single_task_closure(above, exact).solver_status is core.V93SolverStatus.NO_CANDIDATE
    assert _single_task_closure(exact, below).solver_status is core.V93SolverStatus.NO_CANDIDATE
    assert _single_task_closure(below, exact).solver_status is core.V93SolverStatus.CANDIDATE


def test_service_curve_contract_and_exact_json_round_trip(tmp_path):
    values = tuple(
        exact_energy.materialize_supply_lower_bound(value, f"trace[{index}]").exact_value
        for index, value in enumerate((0.0, 0.1, 0.2, 0.3))
    )
    beta = exact_energy.service_curve_lower_bound(values, len(values))
    assert beta[0] == 0
    assert all(value >= 0 for value in beta)
    assert all(left <= right for left, right in zip(beta, beta[1:]))

    identity = exact_energy.exact_input_identity(
        task_powers=(("0", Fraction.from_float(0.1)),),
        e0=Fraction(1, 3),
        service_prefix=beta,
    )
    path = tmp_path / "exact.json"
    atomic_write_json(path, {
        "values": [exact_energy.fraction_text(value) for value in beta],
        "identity": identity,
    })
    restored = json.loads(path.read_text(encoding="utf-8"))
    restored_values = tuple(
        exact_energy.parse_persisted_fraction(value, "restored service")
        for value in restored["values"]
    )
    assert restored_values == beta
    assert exact_energy.exact_input_identity(
        task_powers=(("0", Fraction.from_float(0.1)),),
        e0=Fraction(1, 3),
        service_prefix=restored_values,
    ) == restored["identity"] == identity


def test_production_service_curve_raw_spec_round_trips_exactly(tmp_path):
    config = load_config(
        ROOT / "configs/v9_3_core1_smoke.yaml", expected_core="CORE-1",
    )
    service = prepare_service_curve(config, tmp_path / "service")
    raw = json.loads(service.raw_spec)
    restored = tuple(
        exact_energy.parse_persisted_fraction(value, "stored beta")
        for value in raw["validated_prefix"]
    )
    assert restored == service.values
    assert raw["numeric_contract"] == exact_energy.numeric_contract_metadata()


def test_legacy_result_header_is_rejected_without_mutation(tmp_path):
    root = tmp_path / "legacy-results"
    root.mkdir()
    path = root / "analysis_requests.csv"
    legacy = "request_id,analysis_id,cell_id,taskset_id,taskset_hash\nold,a,c,t,h\n"
    path.write_text(legacy, encoding="utf-8")
    with pytest.raises(ResultWriterError, match="header mismatch"):
        ResultWriter(root)
    assert path.read_text(encoding="utf-8") == legacy
    assert sorted(item.name for item in root.iterdir()) == ["analysis_requests.csv"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_invalid_materialized_values_fail_closed(invalid):
    with pytest.raises(exact_energy.ExactEnergyError):
        exact_energy.materialize_demand_upper_bound(invalid, "invalid demand")
    with pytest.raises(exact_energy.ExactEnergyError):
        exact_energy.materialize_supply_lower_bound(invalid, "invalid supply")


def test_raw_float_and_missing_numeric_contract_never_certify():
    with pytest.raises(core.V93InputError, match="must be exact"):
        core.V93Task("bad", 1, 1, 2, 0.1)
    target = core.V93Task("k", 1, 1, 2, Fraction(1))
    with pytest.raises(core.V93InputError):
        core.canonical_closure_search_v9_3(
            core.EnvelopeKind.COMPLETE,
            target,
            (),
            (),
            1,
            {},
            1.0,
            (Fraction(0),),
        )

    legacy_context = taskset.DependencyContext(
        taskset_identity="taskset",
        task_definitions_identity="definitions",
        priority_order_identity="priority",
        e0_canonical_identity="e0",
        service_curve_identity="service",
        power_vector_identity="power",
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
    )
    result = taskset.analyze_taskset_v9_3(
        "legacy-input",
        taskset.AnalysisVariant.CW_THETA_CW,
        taskset.TasksetAnalysisInput(
            (target,), 1, Fraction(1), (Fraction(0),), legacy_context,
        ),
    )
    assert result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR
    assert result.certification_status is taskset.AnalysisCertificationStatus.NOT_CERTIFIED
    assert result.taskset_proven is False


@pytest.mark.parametrize(
    "beta",
    [
        (Fraction(0), Fraction(-1), Fraction(1)),
        (Fraction(0), Fraction(2), Fraction(1)),
        (Fraction(0), float("nan"), Fraction(1)),
        (Fraction(0), float("inf"), Fraction(1)),
    ],
)
def test_illegal_service_curve_returns_numeric_error_not_certification(beta):
    target = core.V93Task("k", 1, 3, 3, Fraction(1))
    context = taskset.DependencyContext(
        taskset_identity="taskset",
        task_definitions_identity="definitions",
        priority_order_identity="priority",
        e0_canonical_identity="e0",
        service_curve_identity="service",
        power_vector_identity="power",
        numerical_mode="EXACT_RATIONAL",
        numerical_scale=None,
        theory_document_sha256=taskset.THEORY_DOCUMENT_SHA256,
        fixed_carry_in_interface_sha256=taskset.FIXED_CARRY_IN_INTERFACE_SHA256,
        numeric_contract_sha256=exact_energy.NUMERIC_CONTRACT_SHA256,
        source_numeric_model=exact_energy.SOURCE_NUMERIC_MODEL,
        demand_rounding_mode=exact_energy.DEMAND_ROUNDING_MODE,
        supply_rounding_mode=exact_energy.SUPPLY_ROUNDING_MODE,
        e0_rounding_mode=exact_energy.E0_ROUNDING_MODE,
        exact_input_identity="exact-input",
        float_decision_path=False,
    )
    result = taskset.analyze_taskset_v9_3(
        "invalid-service",
        taskset.AnalysisVariant.CW_THETA_CW,
        taskset.TasksetAnalysisInput((target,), 1, Fraction(1), beta, context),
    )
    assert result.solver_status is taskset.AnalysisSolverStatus.NUMERIC_ERROR
    assert result.certification_status is taskset.AnalysisCertificationStatus.NOT_CERTIFIED
    assert result.taskset_proven is False


def test_service_accumulation_overflow_is_rejected():
    largest = Fraction.from_float(float.fromhex("0x1.fffffffffffffp+1023"))
    with pytest.raises(exact_energy.ExactEnergyError, match="overflowed"):
        exact_energy.service_curve_lower_bound((largest, largest), 2)


def test_integer_energy_microcase_preserves_parameters_and_result():
    legacy_power = Fraction(str(2.0))
    materialized_power = exact_energy.materialize_task_demand_upper_bound(
        base_power=2000.0,
        workload_coefficient=1.0,
        frequency_ratio=1.0,
        wcet=7,
        label="integer task",
    ).exact_value
    assert materialized_power == legacy_power == 2
    legacy_task = core.V93Task("k", 7, 9, 11, legacy_power)
    exact_task = core.V93Task("k", 7, 9, 11, materialized_power)
    assert (exact_task.wcet, exact_task.deadline, exact_task.period) == (7, 9, 11)
    legacy_result = core.canonical_closure_search_v9_3(
        core.EnvelopeKind.COMPLETE,
        legacy_task,
        (),
        (),
        1,
        {},
        Fraction(20),
        (Fraction(0),) * 9,
    )
    exact_result = core.canonical_closure_search_v9_3(
        core.EnvelopeKind.COMPLETE,
        exact_task,
        (),
        (),
        1,
        {},
        Fraction(20),
        (Fraction(0),) * 9,
    )
    assert exact_result == legacy_result


def test_theory_and_numeric_contract_identity_are_full_and_verified():
    exact_energy.verify_theory_document(ROOT)
    assert len(exact_energy.THEORY_DOCUMENT_SHA256) == 64
    assert len(exact_energy.NUMERIC_CONTRACT_SHA256) == 64
    assert taskset.THEORY_DOCUMENT_SHA256 == exact_energy.THEORY_DOCUMENT_SHA256
    assert taskset.LEGACY_THEORY_DOCUMENT_SHA256 != taskset.THEORY_DOCUMENT_SHA256


def test_core1_methods_and_frozen_generation_seed_are_unchanged():
    config = load_config(ROOT / "configs/v9_3_core1_smoke.yaml", expected_core="CORE-1")
    assert config["analysis"]["variants"] == ["CW_THETA_CW", "LOC_THETA_LOC"]
    cell = expand_cells(config)[0]
    assert cell.generation_id == "a11981d1b910d9809bb6cbca0278d059069322c4d37ddef316801e68090932a0"
    assert derive_seed(
        config["grid"]["base_seed"],
        cell.generation_id,
        0,
        seed_mode=config["grid"].get("seed_mode", "generation_dimensions"),
        utilization_index=cell.utilization_index,
    ) == 285064531


def test_python_and_cpp_binary64_operation_order_match(tmp_path):
    source = tmp_path / "crosscheck.cpp"
    binary = tmp_path / "crosscheck"
    source.write_text(
        r'''
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>

static std::uint64_t bits(double value) {
    std::uint64_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

static double demand(double base, double coefficient, double ratio, int wcet) {
    const double power = base * coefficient * ratio;
    const double wcet_seconds = static_cast<double>(wcet) * 0.001;
    double total = power * wcet_seconds;
    total *= 1.0;
    return total / static_cast<double>(wcet);
}

static double supply(double base, double area, double efficiency, double factor) {
    const double peak = base / (area * efficiency);
    const double irradiance = peak * factor;
    const double elapsed = 1.0 * 0.001;
    return irradiance * area * efficiency * elapsed;
}

static double accumulated_supply(double value) {
    double total = 0.0;
    for (int i = 0; i < 17; ++i) {
        total += value;
    }
    return total;
}

int main() {
    std::cout << std::hex
              << bits(demand(0.5, 1.2, 0.93, 1)) << "\n"
              << bits(demand(0.5, 0.1, 0.93, 7)) << "\n"
              << bits(demand(0.37, 0.42, 0.77, 997)) << "\n"
              << bits(supply(0.054, 1.0, 0.18, 1.0 / 60.0)) << "\n"
              << bits(accumulated_supply(0.1 * 0.2)) << "\n";
}
'''.lstrip(),
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-O0", "-ffp-contract=off", str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = [int(line, 16) for line in subprocess.run(
        [str(binary)], check=True, capture_output=True, text=True,
    ).stdout.splitlines()]

    def demand(base, coefficient, ratio, wcet):
        power = base * coefficient * ratio
        wcet_seconds = float(wcet) * 0.001
        total = power * wcet_seconds
        total *= 1.0
        return total / float(wcet)

    def supply(base, area, efficiency, factor):
        peak = base / (area * efficiency)
        irradiance = peak * factor
        elapsed = 1.0 * 0.001
        return irradiance * area * efficiency * elapsed

    expected = [
        _bits(demand(0.5, 1.2, 0.93, 1)),
        _bits(demand(0.5, 0.1, 0.93, 7)),
        _bits(demand(0.37, 0.42, 0.77, 997)),
        _bits(supply(0.054, 1.0, 0.18, 1.0 / 60.0)),
    ]
    accumulated = 0.0
    for _ in range(17):
        accumulated += 0.1 * 0.2
    expected.append(_bits(accumulated))
    assert observed == expected
    for index, (base, coefficient, ratio, wcet) in enumerate((
        (0.5, 1.2, 0.93, 1),
        (0.5, 0.1, 0.93, 7),
        (0.37, 0.42, 0.77, 997),
    )):
        exact = exact_energy.materialize_task_demand_upper_bound(
            base_power=base,
            workload_coefficient=coefficient,
            frequency_ratio=ratio,
            wcet=wcet,
            label=f"cross-check demand {index}",
        ).exact_value
        assert exact == Fraction.from_float(demand(base, coefficient, ratio, wcet))
        assert _bits(float(exact)) == observed[index]
