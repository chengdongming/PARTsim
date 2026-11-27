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

from experiments.pipeline import system_model as legacy_rta
from experiments.pipeline import task_model as core
from experiments.pipeline import exact_energy
from experiments.pipeline.cell_model import derive_seed, expand_cells
from experiments.pipeline.config import ConfigError, load_config, validate_config
from experiments.pipeline.result_writer import atomic_write_json
from experiments.pipeline.taskset_store import prepare_service_curve


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
        (ROOT / "test/fixtures/task_generation.yaml").read_text(encoding="utf-8")
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




def _reference_slow_variation_primitive(time_ms: int) -> Fraction:
    if time_ms <= 0:
        return Fraction(0)
    if time_ms <= 10000:
        return Fraction(time_ms) * (
            Fraction(1) + (Fraction(1) - Fraction(time_ms, 50000))
        ) / 2
    area = Fraction(9000)
    if time_ms <= 20000:
        return area + Fraction(time_ms - 10000) * Fraction(4, 5)
    area += Fraction(8000)
    if time_ms <= 40000:
        start = Fraction(4, 5)
        end = Fraction(4, 5) + Fraction(time_ms - 20000, 50000)
        return area + Fraction(time_ms - 20000) * (start + end) / 2
    area += Fraction(20000)
    if time_ms <= 50000:
        return area + Fraction(time_ms - 40000) * Fraction(6, 5)
    area += Fraction(12000)
    end = Fraction(6, 5) - Fraction(time_ms - 50000, 50000)
    return area + Fraction(time_ms - 50000) * (Fraction(6, 5) + end) / 2


def _reference_slow_variation_harvest(
    config: legacy_rta.RTASystemConfig, tick: int,
) -> float:
    area_ms = (
        _reference_slow_variation_primitive(tick + 1)
        - _reference_slow_variation_primitive(tick)
    )
    return config.base_harvesting_rate * float(area_ms) * legacy_rta.TICK_SECONDS


def test_harvest_materializer_matches_independent_slow_variation_reference():
    system = legacy_rta.load_system_config(
        str(ROOT / "configs/templates/system_config_unified_template.yml")
    )
    system = replace(
        system,
        use_real_solar_data=False,
        base_harvesting_rate=0.054,
    )
    trace = legacy_rta._harvest_trace_from_config(system, 60002)
    compared_ticks = (
        0, 9999, 10000, 19999, 20000, 29999, 30000, 39999,
        40000, 49999, 50000, 59999, 60000, 60001,
    )
    # The independent oracle integrates with exact Fractions and converts at
    # the assertion boundary; the production mirror follows the C++ binary64
    # operation order. This is the explicit cross-language tolerance.
    assert all(
        math.isclose(
            trace[tick], _reference_slow_variation_harvest(system, tick),
            rel_tol=0.0, abs_tol=4e-16,
        )
        for tick in compared_ticks
    )


def test_synthetic_slow_variation_ignores_calendar_and_pv_parameters():
    system = legacy_rta.load_system_config(
        str(ROOT / "configs/templates/system_config_unified_template.yml")
    )
    reference = replace(
        system,
        use_real_solar_data=False,
        base_harvesting_rate=0.123,
    )
    changed = replace(
        reference,
        day_of_year=999,
        time_of_day_ms=23 * 3600000,
        pv_area_m2=0.037,
        pv_efficiency=0.213,
    )
    assert legacy_rta._harvest_trace_from_config(reference, 60001) == (
        legacy_rta._harvest_trace_from_config(changed, 60001)
    )


def test_synthetic_slow_variation_beta_dominates_date22_reference():
    system = legacy_rta.load_system_config(
        str(ROOT / "configs/templates/system_config_unified_template.yml")
    )
    system = replace(system, use_real_solar_data=False, base_harvesting_rate=1.0)
    trace = [Fraction.from_float(value)
             for value in legacy_rta._harvest_trace_from_config(system, 60000)]
    prefix = [Fraction(0)]
    for value in trace:
        prefix.append(prefix[-1] + value)
    selected = (1, 2, 10, 100, 1000, 5000, 10000, 10001,
                20000, 30000, 40000, 50000, 60000)
    for delta in selected:
        doubled = trace + trace[:delta]
        doubled_prefix = [Fraction(0)]
        for value in doubled:
            doubled_prefix.append(doubled_prefix[-1] + value)
        beta = min(
            doubled_prefix[start + delta] - doubled_prefix[start]
            for start in range(60000)
        )
        reference = Fraction(4, 5) * Fraction(1, 1000) * max(
            Fraction(delta) - Fraction(2, 5), Fraction(0)
        )
        assert beta >= reference
    assert math.isclose(float(sum(trace)), 60.0, rel_tol=0.0, abs_tol=1e-12)




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
        ROOT / "test/fixtures/task_generation.yaml", expected_core="CORE-1",
    )
    service = prepare_service_curve(config, tmp_path / "service")
    raw = json.loads(service.raw_spec)
    restored = tuple(
        exact_energy.parse_persisted_fraction(value, "stored beta")
        for value in raw["validated_prefix"]
    )
    assert restored == service.values
    assert raw["numeric_contract"] == exact_energy.numeric_contract_metadata()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_invalid_materialized_values_fail_closed(invalid):
    with pytest.raises(exact_energy.ExactEnergyError):
        exact_energy.materialize_demand_upper_bound(invalid, "invalid demand")
    with pytest.raises(exact_energy.ExactEnergyError):
        exact_energy.materialize_supply_lower_bound(invalid, "invalid supply")






def test_service_accumulation_overflow_is_rejected():
    largest = Fraction.from_float(float.fromhex("0x1.fffffffffffffp+1023"))
    with pytest.raises(exact_energy.ExactEnergyError, match="overflowed"):
        exact_energy.service_curve_lower_bound((largest, largest), 2)






def test_core1_methods_and_frozen_generation_seed_are_unchanged():
    config = load_config(ROOT / "test/fixtures/task_generation.yaml", expected_core="CORE-1")
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
