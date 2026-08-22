#!/usr/bin/env python3
"""Analyze Figure 1 RTA-LOAD-CROSS output at one fixed energy scale."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.parallel_prepare import run_independent_jobs, validate_workers


METHOD_ORDER = ("CW", "LOC", "PH", "SEQ")
KNOWN_STATUSES = (
    "PROVEN", "UNPROVEN_TIMEOUT", "NOT_PROVEN", "NUMERIC_ERROR",
    "INTERNAL_ERROR",
)
SUMMARY_FIELDS = (
    "target_uc", "e0", "method", "n_total", "n_proven",
    "acceptance_ratio", "n_timeout", "timeout_ratio", "n_no_candidate",
    "n_numeric_error", "n_internal_error",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"required input is missing: {path.name}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {path.name} line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path.name} line {line_number} is not a JSON object"
                )
            rows.append(value)
    return rows


def _fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be an exact rational")
    try:
        return Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be an exact rational: {value!r}") from exc


def _fraction_text(value: Any, label: str = "value") -> str:
    exact = _fraction(value, label)
    return str(exact.numerator) if exact.denominator == 1 else (
        f"{exact.numerator}/{exact.denominator}"
    )


def _required_list(source: dict[str, Any], key: str) -> list[Any]:
    value = source.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"run_config field {key!r} must be a non-empty list")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    try:
        run_config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid run_config.json: {exc}") from exc
    if not isinstance(run_config, dict):
        raise ValueError("run_config.json must contain an object")
    source = run_config.get("semantic_config", run_config)
    if not isinstance(source, dict):
        raise ValueError("run_config.semantic_config must contain an object")
    if source.get("energy_mode") != "fixed_scale":
        raise ValueError(
            "run_config is not a fixed-scale experiment: "
            f"energy_mode={source.get('energy_mode')!r}"
        )

    energy_scale = _fraction(source.get("energy_scale"), "energy_scale")
    if energy_scale < 0:
        raise ValueError("energy_scale must be non-negative")
    uc_values = []
    for index, value in enumerate(_required_list(source, "uc_values")):
        exact = _fraction(value, f"uc_values[{index}]")
        if not 0 < exact <= 1:
            raise ValueError("uc_values must be in the open/closed range (0, 1]")
        if exact not in uc_values:
            uc_values.append(exact)
    if len(uc_values) != len(source["uc_values"]):
        raise ValueError("uc_values must not contain duplicates")

    e0_values = []
    for index, value in enumerate(_required_list(source, "e0_values")):
        exact = _fraction(value, f"e0_values[{index}]")
        if exact < 0:
            raise ValueError("e0_values must be non-negative")
        if exact not in e0_values:
            e0_values.append(exact)
    if len(e0_values) != len(source["e0_values"]):
        raise ValueError("e0_values must not contain duplicates")

    methods = [str(value).upper() for value in _required_list(source, "methods")]
    if any(value not in METHOD_ORDER for value in methods):
        raise ValueError("methods must be a subset of CW,LOC,PH,SEQ")
    if len(methods) != len(set(methods)):
        raise ValueError("methods must not contain duplicates")
    samples = source.get("samples_per_uc")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise ValueError("samples_per_uc must be a positive integer")
    return {
        "energy_scale": energy_scale,
        "uc_values": tuple(sorted(uc_values)),
        "e0_values": tuple(sorted(e0_values)),
        "methods": tuple(method for method in METHOD_ORDER if method in methods),
        "samples_per_uc": samples,
    }


def _taskset_metadata(
    tasksets: list[dict[str, Any]], config: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    by_id: dict[str, dict[str, Any]] = {}
    uc_counts: Counter[str] = Counter()
    duplicate_count = 0
    invalid_count = 0
    configured_uc = set(_fraction_text(value) for value in config["uc_values"])
    for row in tasksets:
        taskset_id = row.get("taskset_id")
        if not isinstance(taskset_id, str) or not taskset_id:
            invalid_count += 1
            continue
        if taskset_id in by_id:
            duplicate_count += 1
            continue
        try:
            target_uc = _fraction_text(row.get("target_uc"), "taskset target_uc")
        except ValueError:
            invalid_count += 1
            continue
        if target_uc not in configured_uc:
            invalid_count += 1
            continue
        if row.get("energy_mode", "fixed_scale") != "fixed_scale":
            invalid_count += 1
            continue
        if "energy_scale" in row:
            try:
                if _fraction(row["energy_scale"], "taskset energy_scale") != config["energy_scale"]:
                    invalid_count += 1
                    continue
            except ValueError:
                invalid_count += 1
                continue
        by_id[taskset_id] = {**row, "target_uc": target_uc}
        uc_counts[target_uc] += 1
    return by_id, {
        "duplicate_taskset_id_count": duplicate_count,
        "invalid_taskset_count": invalid_count,
        "taskset_coverage_violation_count": sum(
            uc_counts[_fraction_text(value)] != config["samples_per_uc"]
            for value in config["uc_values"]
        ),
    }


def _result_key(row: dict[str, Any]) -> tuple[str, str, str]:
    taskset_id = row.get("taskset_id")
    method = str(row.get("method", "")).upper()
    if not isinstance(taskset_id, str) or not taskset_id or not method:
        raise ValueError("result requires taskset_id, e0, and method")
    return taskset_id, _fraction_text(row.get("e0"), "result e0"), method


def _numeric_vector(value: Any) -> tuple[Fraction, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        values = tuple(_fraction(item, "response time") for item in value)
    except ValueError:
        return None
    return values if all(item >= 0 for item in values) else None


def _complete_unique_results(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            grouped[_result_key(row)].append(row)
        except ValueError:
            continue
    return {
        key: values[0] for key, values in grouped.items() if len(values) == 1
    }


def _summary_rows(
    records: list[tuple[tuple[str, str, str], dict[str, Any]]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, row in records:
        taskset_id, e0, method = key
        groups[(row["_target_uc"], e0, method)].append(row)
    summary = []
    for uc in config["uc_values"]:
        uc_text = _fraction_text(uc)
        for e0 in config["e0_values"]:
            e0_text = _fraction_text(e0)
            for method in config["methods"]:
                group = groups.get((uc_text, e0_text, method), [])
                statuses = [row.get("final_status") for row in group]
                total = len(group)
                proven = statuses.count("PROVEN")
                summary.append({
                    "target_uc": uc_text, "e0": e0_text, "method": method,
                    "n_total": total, "n_proven": proven,
                    "acceptance_ratio": proven / total if total else 0.0,
                    "n_timeout": statuses.count("UNPROVEN_TIMEOUT"),
                    "timeout_ratio": statuses.count("UNPROVEN_TIMEOUT") / total if total else 0.0,
                    "n_no_candidate": statuses.count("NOT_PROVEN"),
                    "n_numeric_error": statuses.count("NUMERIC_ERROR"),
                    "n_internal_error": statuses.count("INTERNAL_ERROR"),
                })
    return summary


def _invariants(
    records: list[tuple[tuple[str, str, str], dict[str, Any]]],
    config: dict[str, Any],
) -> dict[str, int]:
    counts: Counter[tuple[str, str, str]] = Counter(key for key, _row in records)
    unique = _complete_unique_results(row for _key, row in records)
    dominance = 0
    nesting = 0
    chain = ("CW", "LOC"), ("LOC", "PH"), ("PH", "SEQ")
    for taskset_id, e0 in {
        (taskset_id, e0) for taskset_id, e0, _method in unique
    }:
        for stronger, weaker in chain:
            if stronger not in config["methods"] or weaker not in config["methods"]:
                continue
            left = unique.get((taskset_id, e0, stronger))
            right = unique.get((taskset_id, e0, weaker))
            if left is None or right is None:
                continue
            if left.get("final_status") == "PROVEN" and right.get("final_status") != "PROVEN":
                nesting += 1
            if left.get("final_status") != "PROVEN" or right.get("final_status") != "PROVEN":
                continue
            left_vector = _numeric_vector(left.get("response_time_vector"))
            right_vector = _numeric_vector(right.get("response_time_vector"))
            if left_vector is not None and right_vector is not None and len(left_vector) == len(right_vector):
                if any(right_value > left_value for left_value, right_value in zip(left_vector, right_vector)):
                    dominance += 1

    monotonicity = 0
    inconclusive = 0
    e0_values = tuple(config["e0_values"])
    if len(e0_values) >= 2:
        taskset_methods = {
            (taskset_id, method)
            for taskset_id, _e0, method in unique
        }
        for taskset_id, method in taskset_methods:
            for low_e0, high_e0 in zip(e0_values, e0_values[1:]):
                low = _fraction_text(low_e0)
                high = _fraction_text(high_e0)
                low_row = unique.get((taskset_id, low, method))
                high_row = unique.get((taskset_id, high, method))
                if low_row is None or high_row is None:
                    continue
                low_status = low_row.get("final_status")
                high_status = high_row.get("final_status")
                if low_status not in {"PROVEN", "NOT_PROVEN"} or high_status not in {"PROVEN", "NOT_PROVEN"}:
                    inconclusive += 1
                    continue
                if low_status == "PROVEN" and high_status == "NOT_PROVEN":
                    monotonicity += 1
                    continue
                if low_status == "PROVEN" and high_status == "PROVEN":
                    low_vector = _numeric_vector(low_row.get("response_time_vector"))
                    high_vector = _numeric_vector(high_row.get("response_time_vector"))
                    if low_vector is not None and high_vector is not None and len(low_vector) == len(high_vector):
                        if any(high_value > low_value for low_value, high_value in zip(low_vector, high_vector)):
                            monotonicity += 1

    return {
        "dominance_violation_count": dominance,
        "certification_nesting_violation_count": nesting,
        "e0_monotonicity_violation_count": monotonicity,
        "inconclusive_monotonicity_pairs": inconclusive,
        "duplicate_result_key_count": sum(value > 1 for value in counts.values()),
    }


def _plot(summary: list[dict[str, Any]], output: Path, config: dict[str, Any], e0: Fraction) -> None:
    e0_text = _fraction_text(e0)
    figure, axis = plt.subplots(figsize=(6, 4.5))
    for method in config["methods"]:
        points = [
            row for row in summary
            if row["e0"] == e0_text and row["method"] == method
        ]
        points.sort(key=lambda row: _fraction(row["target_uc"], "target_uc"))
        axis.plot(
            [float(_fraction(row["target_uc"], "target_uc")) for row in points],
            [row["acceptance_ratio"] for row in points],
            marker="o", label=method,
        )
    axis.set_xlabel("U_C")
    axis.set_ylabel("RTA certification ratio")
    axis.set_title(
        "RTA certification ratio versus U_C\n"
        f"fixed energy scale = {_fraction_text(config['energy_scale'])}; E0 = {e0_text}"
    )
    axis.set_ylim(0, 1.05)
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    filename_e0 = e0_text.replace("/", "_").replace("-", "m")
    figure.savefig(
        output / f"figure_uc_fixed_scale_e0_{filename_e0}.png", dpi=150
    )
    plt.close(figure)


def _plot_job(job: dict[str, Any]) -> None:
    _plot(job["summary"], Path(job["output"]), job["config"], Fraction(job["e0"]))


def analyze(output: Path, *, analysis_workers: int = 1) -> tuple[int, dict[str, Any]]:
    validate_workers(analysis_workers, "analysis-workers")
    analysis_started = time.perf_counter()
    validation_started = analysis_started
    config = _load_config(output / "run_config.json")
    tasksets = _read_jsonl(output / "tasksets.jsonl")
    results = _read_jsonl(output / "results.jsonl")
    taskset_by_id, taskset_counts = _taskset_metadata(tasksets, config)
    valid_ids = set(taskset_by_id)
    expected_keys = {
        (taskset_id, _fraction_text(e0), method)
        for taskset_id in valid_ids
        for e0 in config["e0_values"]
        for method in config["methods"]
    }

    records: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
    observed_keys: set[tuple[str, str, str]] = set()
    request_ids = []
    invalid_result_count = 0
    result_metadata_mismatch_count = 0
    invalid_status_count = 0
    for row in results:
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            invalid_result_count += 1
        else:
            request_ids.append(request_id)
        try:
            key = _result_key(row)
        except ValueError:
            invalid_result_count += 1
            continue
        observed_keys.add(key)
        taskset_id, _e0, method = key
        status = row.get("final_status")
        if status not in KNOWN_STATUSES:
            invalid_status_count += 1
        if taskset_id in taskset_by_id:
            expected_uc = taskset_by_id[taskset_id]["target_uc"]
            try:
                if _fraction_text(row.get("target_uc"), "result target_uc") != expected_uc:
                    result_metadata_mismatch_count += 1
            except ValueError:
                result_metadata_mismatch_count += 1
            row = {**row, "_target_uc": expected_uc}
            records.append((key, row))

    missing_keys = expected_keys - observed_keys
    unexpected_keys = observed_keys - expected_keys
    duplicate_request_ids = {
        value for value, count in Counter(request_ids).items()
        if count > 1
    }
    duplicate_result_key_count = sum(
        count > 1 for count in Counter(key for key, _row in records).values()
    )
    denominator_violation_count = 0
    group_counts = Counter(
        (row["_target_uc"], key[1], key[2]) for key, row in records
    )
    for uc in config["uc_values"]:
        uc_text = _fraction_text(uc)
        expected_denominator = sum(
            row["target_uc"] == uc_text for row in taskset_by_id.values()
        )
        for e0 in config["e0_values"]:
            for method in config["methods"]:
                if group_counts[(uc_text, _fraction_text(e0), method)] != expected_denominator:
                    denominator_violation_count += 1

    summary = _summary_rows(records, config)
    invariants = _invariants(records, config)
    invariants.update({
        "denominator_violation_count": denominator_violation_count,
        "missing_method_or_results_count": len(missing_keys),
        "duplicate_result_key_count": duplicate_result_key_count,
        "duplicate_request_id_count": len(duplicate_request_ids),
        "unexpected_result_count": len(unexpected_keys),
        "n_tasksets": len(tasksets),
        "n_results": len(results),
        "invalid_result_count": invalid_result_count,
        "invalid_status_count": invalid_status_count,
        "result_metadata_mismatch_count": result_metadata_mismatch_count,
        **taskset_counts,
    })
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary)
    validation_summary_seconds = time.perf_counter() - validation_started
    plot_started = time.perf_counter()
    run_independent_jobs(
        [
            {"summary": summary, "output": str(output), "config": config, "e0": e0}
            for e0 in config["e0_values"]
        ],
        _plot_job, workers=analysis_workers,
    )
    plot_seconds = time.perf_counter() - plot_started
    invariants["telemetry"] = {
        "validation_summary_seconds": validation_summary_seconds,
        "plot_seconds": plot_seconds,
        "analysis_total_seconds": time.perf_counter() - analysis_started,
        "analysis_workers": analysis_workers,
    }
    (output / "invariant_report.json").write_text(
        json.dumps(invariants, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "analysis_report.json").write_text(
        json.dumps(invariants, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    errors = (
        invariants["denominator_violation_count"]
        or invariants["missing_method_or_results_count"]
        or invariants["duplicate_result_key_count"]
        or invariants["duplicate_request_id_count"]
        or invariants["unexpected_result_count"]
        or invariants["dominance_violation_count"]
        or invariants["certification_nesting_violation_count"]
        or invariants["e0_monotonicity_violation_count"]
        or invariants["invalid_result_count"]
        or invariants["invalid_status_count"]
        or invariants["result_metadata_mismatch_count"]
        or invariants["duplicate_taskset_id_count"]
        or invariants["invalid_taskset_count"]
        or invariants["taskset_coverage_violation_count"]
    )
    return (2 if errors else 0), invariants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Figure 1 fixed-scale RTA certification output."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--analysis-workers", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        code, invariants = analyze(
            Path(args.input), analysis_workers=args.analysis_workers,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"fixed-scale RTA analysis failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(invariants, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
