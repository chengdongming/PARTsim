"""Exact-rational energy-conserving trace resampling and scaling."""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict

from .config import domain_hash, fraction_text
from .energy_trace_loader import EnergyTraceError
from .energy_trace_model import CanonicalEnergyTrace, TraceSample


def _assert_contiguous(trace: CanonicalEnergyTrace) -> None:
    for left, right in zip(trace.samples, trace.samples[1:]):
        if left.timestamp_ns + left.interval_duration_ns != right.timestamp_ns:
            raise EnergyTraceError("resampling requires a contiguous trace segment")


def resample_trace(
    trace: CanonicalEnergyTrace,
    target_interval_ns: int,
    *,
    policy: str,
    declared_interpolation: str = "piecewise_constant",
) -> tuple[CanonicalEnergyTrace, Dict[str, Any]]:
    if target_interval_ns <= 0:
        raise EnergyTraceError("target sample interval must be positive")
    if policy not in {"exact_aggregation", "exact_subdivision", "piecewise_constant", "declared_interpolation"}:
        raise EnergyTraceError("unknown resampling policy")
    if policy == "declared_interpolation" and declared_interpolation != "piecewise_constant":
        raise EnergyTraceError("only explicitly declared piecewise_constant interpolation is certified")
    _assert_contiguous(trace)
    source_durations = {sample.interval_duration_ns for sample in trace.samples}
    if policy == "exact_aggregation" and any(target_interval_ns % value for value in source_durations):
        raise EnergyTraceError("exact aggregation requires an integer interval multiple")
    if policy == "exact_subdivision" and any(value % target_interval_ns for value in source_durations):
        raise EnergyTraceError("exact subdivision requires an integer interval divisor")
    start = trace.samples[0].timestamp_ns
    end = trace.samples[-1].timestamp_ns + trace.samples[-1].interval_duration_ns
    if (end - start) % target_interval_ns:
        raise EnergyTraceError("target intervals do not exactly cover the trace")
    output = []
    for output_start in range(start, end, target_interval_ns):
        output_end = output_start + target_interval_ns
        energy = Fraction(0)
        for sample in trace.samples:
            sample_end = sample.timestamp_ns + sample.interval_duration_ns
            overlap = max(0, min(output_end, sample_end) - max(output_start, sample.timestamp_ns))
            if overlap:
                energy += sample.interval_energy_j * Fraction(overlap, sample.interval_duration_ns)
        offset_seconds = Fraction(output_start - start, 1_000_000_000)
        output.append(TraceSample(
            output_start, f"OFFSET+{fraction_text(offset_seconds)}s",
            target_interval_ns, energy, fraction_text(energy), False,
        ))
    result = CanonicalEnergyTrace(
        trace_id=f"{trace.trace_id}:resampled:{target_interval_ns}ns",
        source_id=trace.source_id, source_file_hash=trace.source_file_hash,
        quantity_kind="INTERVAL_ENERGY", physical_unit="J",
        preprocessing_version=f"EXT2_RESAMPLE_V1:{policy}:{declared_interpolation}",
        fixture_label=trace.fixture_label, samples=tuple(output),
    )
    difference = result.total_energy_j - trace.total_energy_j
    if difference != 0:
        raise EnergyTraceError("P0 exact trace resampling violated energy conservation")
    return result, {
        "input_trace_hash": trace.trace_hash, "output_trace_hash": result.trace_hash,
        "policy": policy, "declared_interpolation": declared_interpolation,
        "input_total_energy_j": fraction_text(trace.total_energy_j),
        "output_total_energy_j": fraction_text(result.total_energy_j),
        "difference_j": fraction_text(difference), "rounding_bound_j": "0",
        "status": "EXACT_ENERGY_CONSERVED",
    }


def scale_trace(trace: CanonicalEnergyTrace, scale: Fraction) -> tuple[CanonicalEnergyTrace, Dict[str, Any]]:
    if scale < 0:
        raise EnergyTraceError("trace scale must be non-negative")
    samples = tuple(TraceSample(
        sample.timestamp_ns, sample.timestamp_utc, sample.interval_duration_ns,
        sample.interval_energy_j * scale, sample.original_value, sample.missing_data,
    ) for sample in trace.samples)
    result = CanonicalEnergyTrace(
        trace_id=f"{trace.trace_id}:scale:{fraction_text(scale)}",
        source_id=trace.source_id, source_file_hash=trace.source_file_hash,
        quantity_kind=trace.quantity_kind, physical_unit=trace.physical_unit,
        preprocessing_version=f"{trace.preprocessing_version}:EXACT_SCALE_V1",
        fixture_label=trace.fixture_label, samples=samples,
    )
    return result, {
        "input_trace_hash": trace.trace_hash, "scale": fraction_text(scale),
        "output_trace_hash": result.trace_hash,
        "input_total_energy_j": fraction_text(trace.total_energy_j),
        "output_total_energy_j": fraction_text(result.total_energy_j),
        "total_energy_change_j": fraction_text(result.total_energy_j - trace.total_energy_j),
    }
