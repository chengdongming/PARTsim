from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from experiments.v9_3.energy_trace_loader import load_energy_trace_csv
from experiments.v9_3.energy_trace_resampling import resample_trace, scale_trace
from test_v9_3_ext2_trace_loader import FIXTURE, schema


def test_exact_subdivision_conserves_energy_without_float_accumulation():
    trace = load_energy_trace_csv(FIXTURE, schema())
    output, check = resample_trace(
        trace, 60_000_000_000, policy="exact_subdivision"
    )
    assert len(output.samples) == 6
    assert output.total_energy_j == trace.total_energy_j
    assert check["difference_j"] == "0"
    assert [sample.interval_energy_j for sample in output.samples[:2]] == [60, 60]


def test_exact_aggregation_and_declared_piecewise_constant_are_supported():
    trace = load_energy_trace_csv(FIXTURE, schema())
    split, _ = resample_trace(trace, 60_000_000_000, policy="exact_subdivision")
    joined, _ = resample_trace(split, 120_000_000_000, policy="exact_aggregation")
    interpolated, _ = resample_trace(
        joined, 40_000_000_000, policy="declared_interpolation",
        declared_interpolation="piecewise_constant",
    )
    assert joined.total_energy_j == interpolated.total_energy_j == trace.total_energy_j


def test_exact_rational_scaling_records_hash_and_energy_change():
    trace = load_energy_trace_csv(FIXTURE, schema())
    scaled, record = scale_trace(trace, Fraction(3, 2))
    assert scaled.total_energy_j == 630
    assert record["scale"] == "3/2"
    assert record["input_trace_hash"] != record["output_trace_hash"]
