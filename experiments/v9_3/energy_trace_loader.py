"""Fail-closed CSV loader and repository real-trace inventory."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .config import fraction_text
from .energy_trace_model import (
    CanonicalEnergyTrace, KNOWN_QUANTITY_KINDS, KNOWN_UNITS, TraceSample,
)


class EnergyTraceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fraction(value: Any, label: str) -> Fraction:
    text = str(value).strip()
    if text.lower() in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity"}:
        raise EnergyTraceError(f"{label} must be finite")
    try:
        result = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise EnergyTraceError(f"invalid exact value for {label}: {value!r}") from exc
    return result


def _timestamp(value: str, timestamp_format: str) -> tuple[int, str]:
    if timestamp_format == "unix_ns":
        try:
            nanoseconds = int(value)
        except ValueError as exc:
            raise EnergyTraceError("timestamp must be integer unix nanoseconds") from exc
        moment = datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=timezone.utc)
    elif timestamp_format == "iso8601":
        text = value.strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise EnergyTraceError(f"invalid ISO-8601 timestamp: {value!r}") from exc
        if moment.tzinfo is None:
            raise EnergyTraceError("timestamp timezone must be explicit")
        moment = moment.astimezone(timezone.utc)
        nanoseconds = int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1000
    else:
        raise EnergyTraceError(f"unknown timestamp format: {timestamp_format}")
    return nanoseconds, moment.isoformat().replace("+00:00", "Z")


def _interval_energy(
    value: Fraction,
    duration_ns: int,
    quantity_kind: str,
    unit: str,
    conversion: Mapping[str, Any],
    previous_cumulative_j: Optional[Fraction],
) -> tuple[Fraction, Optional[Fraction]]:
    seconds = Fraction(duration_ns, 1_000_000_000)
    area = _fraction(conversion.get("pv_area_m2", "1"), "pv_area_m2")
    efficiency = _fraction(conversion.get("pv_efficiency", "1"), "pv_efficiency")
    if area <= 0 or efficiency < 0 or efficiency > 1:
        raise EnergyTraceError("PV area/efficiency declaration is invalid")
    if quantity_kind == "INSTANTANEOUS_POWER":
        if unit == "W":
            energy = value * seconds
        elif unit == "mW":
            energy = value * seconds / 1000
        elif unit == "W/m^2":
            energy = value * area * efficiency * seconds
        else:
            raise EnergyTraceError("unit is incompatible with instantaneous power")
        return energy, previous_cumulative_j
    if quantity_kind == "INTERVAL_ENERGY":
        if unit == "J":
            energy = value
        elif unit == "mJ":
            energy = value / 1000
        elif unit == "Wh/m^2":
            energy = value * 3600 * area * efficiency
        else:
            raise EnergyTraceError("unit is incompatible with interval energy")
        return energy, previous_cumulative_j
    if unit not in {"J", "mJ"}:
        raise EnergyTraceError("cumulative energy requires J or mJ")
    cumulative = value if unit == "J" else value / 1000
    baseline = previous_cumulative_j if previous_cumulative_j is not None else Fraction(0)
    if cumulative < baseline:
        raise EnergyTraceError("cumulative energy decreased")
    return cumulative - baseline, cumulative


def load_energy_trace_csv(path: Path, schema: Mapping[str, Any]) -> CanonicalEnergyTrace:
    quantity_kind = str(schema.get("quantity_kind"))
    unit = str(schema.get("unit"))
    if quantity_kind not in KNOWN_QUANTITY_KINDS:
        raise EnergyTraceError("unknown or ambiguous quantity kind")
    if unit not in KNOWN_UNITS:
        raise EnergyTraceError("unknown physical unit")
    missing_marker = schema.get("missing_marker")
    missing_policy = schema.get("missing_policy", "reject")
    if missing_policy not in {"reject", "zero_declared"}:
        raise EnergyTraceError("missing policy must be explicitly reject or zero_declared")
    if not path.is_file():
        raise EnergyTraceError(f"trace file does not exist: {path}")
    timestamp_column = str(schema.get("timestamp_column", "timestamp"))
    duration_column = str(schema.get("duration_column", "duration_seconds"))
    value_column = str(schema.get("value_column", "value"))
    previous_timestamp: Optional[int] = None
    previous_cumulative: Optional[Fraction] = None
    samples = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {timestamp_column, duration_column, value_column}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise EnergyTraceError(f"trace columns missing: {sorted(required)}")
        for row_index, row in enumerate(reader, start=2):
            timestamp_ns, timestamp_utc = _timestamp(
                str(row[timestamp_column]), str(schema.get("timestamp_format", "iso8601"))
            )
            if previous_timestamp is not None and timestamp_ns <= previous_timestamp:
                raise EnergyTraceError("timestamps must be strictly increasing and unique")
            duration = _fraction(row[duration_column], f"duration row {row_index}")
            duration_ns_fraction = duration * 1_000_000_000
            if duration <= 0 or duration_ns_fraction.denominator != 1:
                raise EnergyTraceError("sample interval must be a positive integer nanosecond count")
            raw_value = str(row[value_column]).strip()
            missing = missing_marker is not None and raw_value == str(missing_marker)
            if missing and missing_policy == "reject":
                raise EnergyTraceError(f"missing value rejected at row {row_index}")
            value = Fraction(0) if missing else _fraction(raw_value, f"value row {row_index}")
            if value < 0:
                raise EnergyTraceError("negative harvested power/energy is invalid")
            energy, previous_cumulative = _interval_energy(
                value, duration_ns_fraction.numerator, quantity_kind, unit,
                schema.get("conversion", {}), previous_cumulative,
            )
            if energy < 0:
                raise EnergyTraceError("negative interval energy is invalid")
            samples.append(TraceSample(
                timestamp_ns, timestamp_utc, duration_ns_fraction.numerator,
                energy, raw_value, missing,
            ))
            previous_timestamp = timestamp_ns
    if not samples:
        raise EnergyTraceError("trace contains no samples")
    return CanonicalEnergyTrace(
        trace_id=str(schema["trace_id"]), source_id=str(schema["source_id"]),
        source_file_hash=sha256_file(path), quantity_kind=quantity_kind,
        physical_unit=unit,
        preprocessing_version=str(schema.get("preprocessing_version", "UNDECLARED")),
        fixture_label=schema.get("fixture_label"), samples=tuple(samples),
    )


def repository_trace_inventory(project_root: Path) -> list[Dict[str, Any]]:
    paths = (
        project_root / "data/raw/POWER_Point_Hourly_20250101_20260105_041d79N_123d43E_LST.csv",
        project_root / "data/raw/POWER_Point_Hourly_20250101_20260105_041d79N_123d43E_LST.json",
        project_root / "data/processed/shenyang_solar_minute.csv",
    )
    rows = []
    for path in paths:
        raw = "/raw/" in path.as_posix()
        rows.append({
            "file_name": path.relative_to(project_root).as_posix(),
            "source": "NASA POWER (embedded source header/JSON metadata)" if raw else "Derived from claimed NASA POWER input; transform not recorded",
            "license_or_use": "UNAVAILABLE_IN_REPOSITORY",
            "time_unit": "hourly LST timestamp" if raw else "row index interpreted as one minute by legacy loader",
            "physical_unit": "Wh/m^2 (ALLSKY_SFC_SW_DWN)" if raw else "W/m^2 header; derivation semantics unverified",
            "sampling_interval": "1 hour" if raw else "1 minute",
            "missing_values": "-999 declared in source; final 24 rows include marker" if raw else "legacy loader silently maps any negative value to zero",
            "time_range": "2025-01-01 through 2026-01-05 LST" if raw else "532800 samples; calendar mapping not embedded",
            "paper_release_status": "REAL_TRACE_DATA_UNAVAILABLE",
            "preprocessed": "NO" if raw else "YES_UNVERIFIED_PROVENANCE",
            "sha256": sha256_file(path),
        })
    return rows


INVENTORY_COLUMNS = (
    "file_name", "source", "license_or_use", "time_unit", "physical_unit",
    "sampling_interval", "missing_values", "time_range",
    "paper_release_status", "preprocessed", "sha256",
)
