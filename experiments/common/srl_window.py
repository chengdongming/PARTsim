"""Reject unobserved windows of the bundled SRL minute trace."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

SRL_FILENAME = "srl_edinburg_solar_minute.csv"
MINUTE_MS = 60000
MINUTE = timedelta(minutes=1)


def validate_srl_observed_window(csv_path, start_offset_ms, horizon_ms, *, payload=None):
    """Validate the exact minute rows used by the legacy end-of-slot sampler.

    Applies only to the named SRL dataset. Raw/placeholder data remain
    available for explicit diagnostic or outage experiments.
    """
    path = Path(csv_path)
    if path.name != SRL_FILENAME:
        return
    if (type(start_offset_ms) is not int or start_offset_ms < 0
            or start_offset_ms % MINUTE_MS
            or type(horizon_ms) is not int or horizon_ms <= 0):
        raise ValueError("invalid SRL replay phase or horizon")
    try:
        meta = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        contents = path.read_bytes() if payload is None else payload
        if (meta["schema"] != "PARTSIM_SRL_MINUTE_IRRADIANCE_V1"
                or meta["processed_file"] != path.name
                or meta["processed_sha256"] != hashlib.sha256(contents).hexdigest()
                or meta["sampling_interval_seconds"] != 60):
            raise ValueError("SRL metadata/CSV identity mismatch")
        first = datetime.fromisoformat(meta["first_timestamp"])
        last = datetime.fromisoformat(meta["last_timestamp"])
        total = meta["output_minutes"]
        if (type(total) is not int or total <= 0
                or last != first + (total - 1)*MINUTE
                or contents.count(b"\n") != total + 1):
            raise ValueError("invalid SRL minute axis")
        gaps = []
        for gap in meta["missing_ranges"]:
            a = (datetime.fromisoformat(gap["first"]) - first) // MINUTE
            b = (datetime.fromisoformat(gap["last"]) - first) // MINUTE
            if not 0 <= a <= b < total or gap["minutes"] != b-a+1:
                raise ValueError("invalid SRL missing-data range")
            if gaps and a <= gaps[-1][1]:
                raise ValueError("overlapping or unordered SRL missing-data ranges")
            gaps.append((a,b))
        missing = sum(b-a+1 for a,b in gaps)
        if (missing != meta["missing_minutes"]
                or total-missing != meta["observed_minutes"]):
            raise ValueError("inconsistent SRL missing-data coverage")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot validate SRL data coverage: {exc}") from exc
    # Native legacy sampling uses (offset + tick) // 60000, tick=1..horizon.
    lo = (start_offset_ms + 1) // MINUTE_MS
    hi = (start_offset_ms + horizon_ms) // MINUTE_MS
    if hi >= total:
        raise ValueError("SRL observed window exceeds the data range")
    if any(lo <= b and hi >= a for a,b in gaps):
        raise ValueError("SRL observed window intersects missing-data placeholders")
