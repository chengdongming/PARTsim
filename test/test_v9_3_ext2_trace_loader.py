from __future__ import annotations

from pathlib import Path

import pytest

from experiments.v9_3.energy_trace_loader import EnergyTraceError, load_energy_trace_csv


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/v9_3_ext2_synthetic_trace.csv"


def schema(**changes):
    value = {
        "trace_id": "fixture", "source_id": "unit-test",
        "timestamp_column": "timestamp", "timestamp_format": "iso8601",
        "duration_column": "duration_seconds", "value_column": "power_w",
        "quantity_kind": "INSTANTANEOUS_POWER", "unit": "W",
        "missing_policy": "reject", "preprocessing_version": "NONE",
        "fixture_label": "SYNTHETIC_TEST_FIXTURE",
    }
    value.update(changes)
    return value


def test_loader_preserves_declared_units_and_exact_energy():
    trace = load_energy_trace_csv(FIXTURE, schema())
    assert trace.total_energy_j == 420
    assert trace.fixture_label == "SYNTHETIC_TEST_FIXTURE"


def test_unknown_unit_is_rejected():
    with pytest.raises(EnergyTraceError, match="unknown physical unit"):
        load_energy_trace_csv(FIXTURE, schema(unit="watts-ish"))


@pytest.mark.parametrize("bad_value", ["-1", "NaN", "Inf"])
def test_negative_nan_and_inf_are_rejected(tmp_path, bad_value):
    path = tmp_path / "bad.csv"
    path.write_text(f"timestamp,duration_seconds,power_w\n2025-01-01T00:00:00Z,1,{bad_value}\n", encoding="utf-8")
    with pytest.raises(EnergyTraceError):
        load_energy_trace_csv(path, schema())


def test_duplicate_or_reverse_timestamps_are_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(
        "timestamp,duration_seconds,power_w\n"
        "2025-01-01T00:00:01Z,1,1\n"
        "2025-01-01T00:00:01Z,1,1\n", encoding="utf-8",
    )
    with pytest.raises(EnergyTraceError, match="strictly increasing"):
        load_energy_trace_csv(path, schema())


def test_zero_interval_and_undeclared_missing_fill_are_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("timestamp,duration_seconds,power_w\n2025-01-01T00:00:00Z,0,-999\n", encoding="utf-8")
    with pytest.raises(EnergyTraceError):
        load_energy_trace_csv(path, schema(missing_marker="-999"))
