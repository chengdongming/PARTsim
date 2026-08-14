#!/usr/bin/env python3
"""Analyze CORE-5A timing records without changing the execution protocol."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.v9_3.core5a_standardized_timing import (
    MEASURED_REPETITIONS,
    REPETITIONS,
    Core5ATimingError,
)


TIMEOUT_SECONDS = 1200.0


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise Core5ATimingError(f"{path} must contain an object")
    return document


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        if not isinstance(row, dict):
            raise Core5ATimingError(f"record line {line_number} is not an object")
        rows.append(row)
    return rows


def _km_median_and_rmst(records: Iterable[Mapping[str, Any]], tau: float) -> tuple[float | None, float | None]:
    observations: list[tuple[float, bool]] = []
    for row in records:
        if bool(row.get("error")):
            continue
        if bool(row.get("timeout")):
            observations.append((tau, False))
        else:
            try:
                duration = float(row["runtime_cpu_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if duration < 0:
                continue
            observations.append((min(duration, tau), duration <= tau))
    if not observations:
        return None, None
    observations.sort(key=lambda item: item[0])
    at_times: dict[float, dict[str, int]] = {}
    for time_value, event in observations:
        bucket = at_times.setdefault(time_value, {"events": 0, "censored": 0})
        bucket["events" if event else "censored"] += 1
    at_risk = len(observations)
    survival = 1.0
    median: float | None = None
    area = 0.0
    previous = 0.0
    for time_value, bucket in sorted(at_times.items()):
        area += survival * max(0.0, time_value - previous)
        if bucket["events"]:
            survival *= 1.0 - bucket["events"] / at_risk
            if median is None and survival <= 0.5:
                median = time_value
        at_risk -= bucket["events"] + bucket["censored"]
        previous = time_value
    area += survival * max(0.0, tau - previous)
    return median, area


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def analyze(root: Path) -> dict[str, Any]:
    plan = _read_json(root / "plan.json")
    records = _read_jsonl(root / "timing_records.jsonl")
    expected_rows = plan.get("rows")
    if not isinstance(expected_rows, list):
        raise Core5ATimingError("plan rows are missing")
    expected = {str(row["execution_id"]): row for row in expected_rows}
    observed: dict[str, dict[str, Any]] = {}
    duplicate = 0
    unexpected = 0
    for row in records:
        execution_id = str(row.get("execution_id", ""))
        if execution_id in observed:
            duplicate += 1
        if execution_id not in expected:
            unexpected += 1
        observed[execution_id] = row
    missing = sorted(set(expected) - set(observed))
    partial_repetitions = 0
    partial_methods = 0
    groups: dict[str, set[int]] = {}
    method_groups: dict[tuple[str, str, str], set[int]] = {}
    for row in records:
        math_id = str(row.get("mathematical_request_id", ""))
        groups.setdefault(math_id, set()).add(int(row.get("repetition", -1)))
        key = (str(row.get("axis")), str(row.get("axis_value")), str(row.get("method")))
        method_groups.setdefault(key, set()).add(int(row.get("repetition", -1)))
    partial_repetitions = sum(set(REPETITIONS) != values for values in groups.values())
    partial_methods = sum(
        len(values) not in {0, 3} for values in method_groups.values()
    )
    records_fields = [
        "axis", "axis_value", "task_count", "processors", "time_scale",
        "target_total_utilization", "target_normalized_utilization",
        "taskset_index", "taskset_slot_id", "taskset_identity", "method",
        "mathematical_request_id", "execution_id", "repetition",
        "measurement_class", "exact_e0", "solver_status", "taskset_proven",
        "runtime_cpu_seconds", "runtime_wall_seconds", "peak_rss_bytes",
        "timeout", "error", "error_text",
    ]
    _write_csv(root / "timing_records.csv", records, records_fields)

    measured = [row for row in records if int(row.get("repetition", -1)) in MEASURED_REPETITIONS]
    summary_rows: list[dict[str, Any]] = []
    summary_groups: dict[tuple[str, Any, str], list[dict[str, Any]]] = {}
    for row in measured:
        key = (str(row["axis"]), row["axis_value"], str(row["method"]))
        summary_groups.setdefault(key, []).append(row)
    for (axis, axis_value, method), group in sorted(summary_groups.items()):
        completed = [row for row in group if not row.get("timeout") and not row.get("error")]
        timeouts = sum(bool(row.get("timeout")) for row in group)
        errors = sum(bool(row.get("error")) for row in group)
        median, rmst = _km_median_and_rmst(group, TIMEOUT_SECONDS)
        taskset_ids = sorted({str(row.get("taskset_identity", "")) for row in group})
        completed_cpu = [float(row["runtime_cpu_seconds"]) for row in completed]
        summary_rows.append({
            "axis": axis,
            "axis_value": axis_value,
            "method": method,
            "taskset_id": ";".join(taskset_ids),
            "planned_execution_count": len({
                (str(row["taskset_slot_id"]), int(row["repetition"]))
                for row in expected_rows
                if row["axis"] == axis and row["axis_value"] == axis_value
                and row["method"] == method and row["repetition"] in MEASURED_REPETITIONS
            }),
            "completed_count": len(completed),
            "timeout_count": timeouts,
            "error_count": errors,
            "completion_ratio": (len(completed) / len(group)) if group else 0.0,
            "km_median_runtime_cpu_seconds": "NA" if median is None else median,
            "rmst_runtime_cpu_seconds_truncated_1200": "NA" if rmst is None else rmst,
            "COMPLETED_ONLY_SUPPLEMENTARY_mean_cpu_seconds": (
                "NA" if not completed_cpu else sum(completed_cpu) / len(completed_cpu)
            ),
            "COMPLETED_ONLY_SUPPLEMENTARY_taskset_count": len(taskset_ids),
        })
    summary_fields = [
        "axis", "axis_value", "method", "taskset_id", "planned_execution_count",
        "completed_count", "timeout_count", "error_count", "completion_ratio",
        "km_median_runtime_cpu_seconds", "rmst_runtime_cpu_seconds_truncated_1200",
        "COMPLETED_ONLY_SUPPLEMENTARY_mean_cpu_seconds",
        "COMPLETED_ONLY_SUPPLEMENTARY_taskset_count",
    ]
    _write_csv(root / "timing_summary.csv", summary_rows, summary_fields)
    audit = {
        "protocol": plan.get("protocol"),
        "plan_identity": plan.get("plan_identity"),
        "expected_execution_count": len(expected),
        "observed_execution_count": len(records),
        "missing_execution_count": len(missing),
        "duplicate_execution_count": duplicate,
        "unexpected_execution_count": unexpected,
        "partial_repetition_group_count": partial_repetitions,
        "partial_method_group_count": partial_methods,
        "warmup_excluded_from_summary": True,
        "summary_repetition_class": "MEASURED_REPETITIONS_1_2_ONLY",
        "timeout_right_censored": True,
        "technical_errors_excluded_from_km": True,
        "timeout_seconds": TIMEOUT_SECONDS,
        "solver_status_counts": {
            str(status): sum(row.get("solver_status") == status for row in records)
            for status in sorted({str(row.get("solver_status")) for row in records})
        },
    }
    (root / "timing_audit.json").write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.output_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
