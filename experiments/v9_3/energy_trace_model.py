"""Canonical exact-energy trace model for v9.3 EXT-2."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, Optional, Tuple

from .config import domain_hash, fraction_text


KNOWN_QUANTITY_KINDS = {
    "INSTANTANEOUS_POWER", "INTERVAL_ENERGY", "CUMULATIVE_ENERGY",
}
KNOWN_UNITS = {"W", "mW", "J", "mJ", "Wh/m^2", "W/m^2"}


@dataclass(frozen=True)
class TraceSample:
    timestamp_ns: int
    timestamp_utc: str
    interval_duration_ns: int
    interval_energy_j: Fraction
    original_value: str
    missing_data: bool = False

    def canonical_row(self) -> Dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "timestamp_utc": self.timestamp_utc,
            "interval_duration_ns": self.interval_duration_ns,
            "interval_energy_j": fraction_text(self.interval_energy_j),
            "original_value": self.original_value,
            "missing_data": self.missing_data,
        }


@dataclass(frozen=True)
class CanonicalEnergyTrace:
    trace_id: str
    source_id: str
    source_file_hash: str
    quantity_kind: str
    physical_unit: str
    preprocessing_version: str
    fixture_label: Optional[str]
    samples: Tuple[TraceSample, ...]

    @property
    def total_energy_j(self) -> Fraction:
        return sum((sample.interval_energy_j for sample in self.samples), Fraction(0))

    @property
    def trace_hash(self) -> str:
        return domain_hash("ASAP_BLOCK:V9.3:EXT2:CANONICAL_TRACE:v1", self.document())

    def document(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "source_id": self.source_id,
            "source_file_hash": self.source_file_hash,
            "quantity_kind": self.quantity_kind,
            "physical_unit": self.physical_unit,
            "preprocessing_version": self.preprocessing_version,
            "fixture_label": self.fixture_label,
            "samples": [sample.canonical_row() for sample in self.samples],
        }
