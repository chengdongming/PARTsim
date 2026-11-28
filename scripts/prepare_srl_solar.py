#!/usr/bin/env python3
"""Prepare the UTRGV SRL minute data for the existing PARTsim solar reader."""
import argparse
import csv
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path

HEADER = ["DATE (MM/DD/YYYY)", "CST", "Global Horizontal [W/m^2]"]
MINUTE = timedelta(minutes=1)
# A deliberately narrow range for small negative irradiance observations.
# Reject larger negative sentinels rather than silently treating them as data.
MIN_ACCEPTED = Decimal("-10")


def prepare(source: Path, output: Path, metadata: Path, start: date, end: date):
    if start > end or len({source.resolve(), output.resolve(), metadata.resolve()}) != 3:
        raise ValueError("invalid dates or overlapping input/output paths")
    first = datetime.combine(start, time.min)
    last = datetime.combine(end + timedelta(days=1), time.min) - MINUTE
    expected = first
    values = []
    gaps = []
    observed = negative = 0
    raw_min = raw_max = None
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    with io.StringIO(source_bytes.decode("utf-8-sig"), newline="") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != HEADER:
            raise ValueError("unexpected SRL export header")
        for line, row in enumerate(reader, 2):
            if len(row) != 3:
                raise ValueError(f"row {line}: expected three columns")
            try:
                timestamp = datetime.strptime(f"{row[0]} {row[1]}", "%m/%d/%Y %H:%M")
                irradiance = Decimal(row[2].strip())
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f"row {line}: invalid time or irradiance") from exc
            if not irradiance.is_finite() or irradiance < MIN_ACCEPTED:
                raise ValueError(f"row {line}: nonfinite irradiance or negative sentinel")
            if timestamp < expected or timestamp > last:
                raise ValueError(f"row {line}: duplicated, out-of-order or out-of-range time")
            if observed == 0 and timestamp != first:
                raise ValueError("source does not cover the first requested minute")
            if timestamp > expected:
                count = (timestamp - expected) // MINUTE
                gaps.append({"first": expected.isoformat(timespec="minutes"),
                             "last": (timestamp - MINUTE).isoformat(timespec="minutes"),
                             "minutes": count})
                values.extend(["0"] * count)
            observed += 1
            raw_min = irradiance if raw_min is None else min(raw_min, irradiance)
            raw_max = irradiance if raw_max is None else max(raw_max, irradiance)
            negative += irradiance < 0
            values.append("0" if irradiance <= 0 else format(irradiance, "f"))
            expected = timestamp + MINUTE
    if observed == 0 or expected != last + MINUTE:
        raise ValueError("source does not cover the last requested minute")
    total = (last - first) // MINUTE + 1
    missing = sum(gap["minutes"] for gap in gaps)
    if len(values) != total or observed + missing != total:
        raise RuntimeError("minute axis reconstruction failed")
    payload = ("irradiance_W_per_m2\n" + "\n".join(values) + "\n").encode("ascii")
    report = {
        "schema": "PARTSIM_SRL_MINUTE_IRRADIANCE_V1",
        "source_station": "UTRGV Solar Radiation Lab, Edinburg, Texas, USA",
        "latitude": "26.3059", "longitude": "-98.1716",
        "source_doi": "10.5439/1052555",
        "source_file": source.name,
        "source_sha256": source_hash,
        "column": HEADER[-1], "unit": "W/m^2",
        "timezone": "CST (UTC-06:00; fixed offset)",
        "first_timestamp": first.isoformat(timespec="minutes"),
        "last_timestamp": last.isoformat(timespec="minutes"),
        "sampling_interval_seconds": 60,
        "observed_minutes": observed, "output_minutes": total,
        "missing_minutes": missing, "missing_ranges": gaps,
        "negative_observations_clipped_to_zero": negative,
        "raw_minimum_W_per_m2": str(raw_min),
        "raw_maximum_W_per_m2": str(raw_max),
        "negative_policy": "clip finite values in [-10,0) to zero; reject lower sentinels",
        "gap_policy": "insert zero placeholders; exclude missing-data windows from observed-data experiments",
        "interpolation": "none; minute-indexed zero-order hold in existing simulator",
        "processed_file": output.name,
        "processed_sha256": hashlib.sha256(payload).hexdigest(),
    }
    # Validate the entire source before touching either result.
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    metadata.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.metadata,
                             args.start_date, args.end_date), indent=2))


if __name__ == "__main__":
    main()
