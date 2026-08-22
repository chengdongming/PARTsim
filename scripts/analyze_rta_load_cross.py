#!/usr/bin/env python3
"""Aggregate RTA-LOAD-CROSS JSONL output and draw one-dimensional scans."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.rta_load_cross import fraction_text  # noqa: E402
from experiments.v9_3.parallel_prepare import run_independent_jobs, validate_workers


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _fraction_text(value: object) -> str:
    exact = Fraction(str(value))
    return str(exact.numerator) if exact.denominator == 1 else f"{exact.numerator}/{exact.denominator}"


def _expected_config(run_config: dict) -> dict:
    source = run_config.get("semantic_config", run_config)
    try:
        cells = []
        for row in source["cells"]:
            if isinstance(row, dict):
                uc, ue = row["target_uc"], row["target_ue"]
            else:
                uc, ue = row[0], row[1]
            cells.append((_fraction_text(uc), _fraction_text(ue)))
        return {
            "cells": frozenset(cells),
            "e0_values": tuple(sorted({_fraction_text(value) for value in source["e0_values"]}, key=Fraction)),
            "methods": tuple(sorted({str(value).upper() for value in source["methods"]})),
        }
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"run_config is incomplete: {exc}") from exc


def _plot(rows: list[dict], output: Path, *, x_key: str, fixed_key: str, fixed_values: tuple[str, ...], filename: str, title: str) -> None:
    available = {(row[x_key], row[fixed_key]) for row in rows}
    panels = [value for value in fixed_values if any(pair[1] == value for pair in available)]
    if not panels:
        return
    figure, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 4), squeeze=False)
    for index, fixed in enumerate(panels):
        axis = axes[0][index]
        for method in ("CW", "LOC", "PH", "SEQ"):
            points = sorted((row for row in rows if row[fixed_key] == fixed and row["method"] == method), key=lambda row: float(Fraction(row[x_key])))
            if points:
                axis.plot([float(Fraction(row[x_key])) for row in points], [row["acceptance_ratio"] for row in points], marker="o", label=method)
        axis.set_title(f"{fixed_key}={fixed}")
        axis.set_xlabel(x_key)
        axis.set_ylabel("acceptance ratio")
        axis.set_ylim(0, 1.05)
        axis.grid(True, alpha=0.3)
        axis.legend()
    figure.suptitle(title + " (U_E=1: long-term average supply-demand balance only)")
    figure.tight_layout()
    figure.savefig(output / filename, dpi=150)
    plt.close(figure)


def _plot_job(job: dict) -> None:
    _plot(
        job["rows"], Path(job["output"]), x_key=job["x_key"],
        fixed_key=job["fixed_key"], fixed_values=tuple(job["fixed_values"]),
        filename=job["filename"], title=job["title"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze RTA-LOAD-CROSS JSONL output.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--analysis-workers", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        validate_workers(args.analysis_workers, "analysis-workers")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    output = Path(args.input)
    analysis_started = time.perf_counter()
    validation_started = analysis_started
    try:
        run_config = json.loads((output / "run_config.json").read_text(encoding="utf-8"))
        expected = _expected_config(run_config)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid run_config.json: {exc}", file=sys.stderr)
        return 2
    tasksets = _read_jsonl(output / "tasksets.jsonl")
    results = _read_jsonl(output / "results.jsonl")
    taskset_by_id = {row.get("taskset_id"): row for row in tasksets}
    expected_tasksets = set(taskset_by_id)
    expected_keys = {
        (taskset_id, e0, method)
        for taskset_id, taskset in taskset_by_id.items()
        for e0 in expected["e0_values"]
        for method in expected["methods"]
    }
    observed_keys = {
        (row.get("taskset_id"), _fraction_text(row.get("e0")), str(row.get("method", "")).upper())
        for row in results
    }
    from collections import Counter
    key_counts = Counter(
        (row.get("taskset_id"), _fraction_text(row.get("e0")), str(row.get("method", "")).upper())
        for row in results
    )
    duplicate_keys = {key for key, count in key_counts.items() if count > 1}
    duplicate_request_ids = {
        request_id for request_id, count in Counter(row.get("request_id") for row in results).items()
        if request_id is not None and count > 1
    }
    missing_keys = expected_keys - observed_keys
    extra_keys = observed_keys - expected_keys
    invalid_taskset_cells = {
        taskset_id for taskset_id, taskset in taskset_by_id.items()
        if (_fraction_text(taskset.get("target_uc")), _fraction_text(taskset.get("target_ue"))) not in expected["cells"]
    }
    coverage_errors = bool(
        missing_keys or extra_keys or duplicate_keys or duplicate_request_ids
        or invalid_taskset_cells
    )
    if missing_keys:
        print(f"missing method/results > 0: {len(missing_keys)}", file=sys.stderr)
    if duplicate_keys or duplicate_request_ids:
        print(f"duplicate results > 0: {max(len(duplicate_keys), len(duplicate_request_ids))}", file=sys.stderr)
    if extra_keys:
        print(f"unexpected results > 0: {len(extra_keys)}", file=sys.stderr)
    if invalid_taskset_cells:
        print(f"tasksets outside configured cells > 0: {len(invalid_taskset_cells)}", file=sys.stderr)
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in results:
        groups[(row["target_uc"], row["target_ue"], row["e0"], row["method"])].append(row)
    summary = []
    for key, group in sorted(groups.items()):
        statuses = [row.get("final_status") for row in group]
        summary.append({
            "target_uc": key[0], "target_ue": key[1], "e0": key[2], "method": key[3],
            "n_total": len(group), "n_proven": statuses.count("PROVEN"),
            "acceptance_ratio": statuses.count("PROVEN") / len(group) if group else 0.0,
            "n_timeout": statuses.count("UNPROVEN_TIMEOUT"),
            "timeout_ratio": statuses.count("UNPROVEN_TIMEOUT") / len(group) if group else 0.0,
            "n_no_candidate": statuses.count("NOT_PROVEN"),
            "n_numeric_error": statuses.count("NUMERIC_ERROR"),
            "n_internal_error": statuses.count("INTERNAL_ERROR"),
        })
    denominator_violations = 0
    for cell in {(row["target_uc"], row["target_ue"], row["e0"]) for row in results}:
        expected_n = sum(
            1 for taskset in tasksets
            if taskset.get("target_uc") == cell[0] and taskset.get("target_ue") == cell[1]
        )
        for method in expected["methods"]:
            if len(groups.get((*cell, method), ())) != expected_n:
                denominator_violations += 1
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(summary[0]) if summary else ["target_uc", "target_ue", "e0", "method", "n_total", "n_proven", "acceptance_ratio", "n_timeout", "timeout_ratio", "n_no_candidate", "n_numeric_error", "n_internal_error"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)

    dominance_violations = 0
    certification_nesting_violations = 0
    method_order = tuple(method for method in ("CW", "LOC", "PH", "SEQ") if method in expected["methods"])
    method_pairs = tuple(zip(method_order, method_order[1:]))
    for taskset_e0 in {(row["taskset_id"], row["e0"]) for row in results}:
        vectors = {row["method"]: row.get("response_time_vector") for row in results if (row["taskset_id"], row["e0"]) == taskset_e0 and row.get("final_status") == "PROVEN"}
        for stronger, weaker in (("CW", "LOC"), ("LOC", "PH"), ("PH", "SEQ")):
            if stronger in vectors and weaker in vectors:
                if any(a is None or b is None or b > a for a, b in zip(vectors[stronger], vectors[weaker])):
                    dominance_violations += 1
    for cell in {(row["target_uc"], row["target_ue"], row["e0"]) for row in results}:
        proven = {
            method: {row["taskset_id"] for row in results if (row["target_uc"], row["target_ue"], row["e0"]) == cell and row["method"] == method and row.get("final_status") == "PROVEN"}
            for method in ("CW", "LOC", "PH", "SEQ")
        }
        for stronger, weaker in method_pairs:
            certification_nesting_violations += len(proven[stronger] - proven[weaker])
    monotonicity_violations = 0
    inconclusive_monotonicity_pairs = 0
    if len(expected["e0_values"]) >= 2:
        low_e0, high_e0 = min(expected["e0_values"], key=Fraction), max(expected["e0_values"], key=Fraction)
        comparable_statuses = {"PROVEN", "NOT_PROVEN"}
        pair_groups: dict[tuple[str, str, str, str], dict[str, dict]] = defaultdict(dict)
        for row in results:
            key = (
                row.get("taskset_id"), row.get("target_uc"),
                row.get("target_ue"), str(row.get("method", "")).upper(),
            )
            pair_groups[key][_fraction_text(row.get("e0"))] = row
        for rows_by_e0 in pair_groups.values():
            low = rows_by_e0.get(low_e0)
            high = rows_by_e0.get(high_e0)
            if low is None or high is None:
                continue
            low_status, high_status = low.get("final_status"), high.get("final_status")
            if low_status not in comparable_statuses or high_status not in comparable_statuses:
                inconclusive_monotonicity_pairs += 1
                continue
            if low_status == "PROVEN" and high_status == "NOT_PROVEN":
                monotonicity_violations += 1
            if low_status == "PROVEN" and high_status == "PROVEN":
                low_vector, high_vector = low.get("response_time_vector"), high.get("response_time_vector")
                if low_vector and high_vector and any(
                    a is not None and b is not None and b > a
                    for a, b in zip(low_vector, high_vector)
                ):
                    monotonicity_violations += 1
    invariant = {
        "dominance_violation_count": dominance_violations,
        "certification_nesting_violation_count": certification_nesting_violations,
        "e0_monotonicity_violation_count": monotonicity_violations,
        "inconclusive_monotonicity_pairs": inconclusive_monotonicity_pairs,
        "denominator_violation_count": denominator_violations,
        "missing_method_or_results_count": len(missing_keys),
        "duplicate_result_key_count": len(duplicate_keys),
        "duplicate_request_id_count": len(duplicate_request_ids),
        "unexpected_result_count": len(extra_keys),
        "n_tasksets": len(tasksets),
        "n_results": len(results),
    }
    validation_summary_seconds = time.perf_counter() - validation_started
    plot_started = time.perf_counter()
    plot_jobs = []
    for e0 in expected["e0_values"]:
        e0_rows = [row for row in summary if row["e0"] == e0]
        e0_filename = e0.replace("/", "_").replace("-", "m")
        plot_jobs.extend([
            {
                "rows": e0_rows, "output": str(output), "x_key": "target_uc",
                "fixed_key": "target_ue", "fixed_values": ("1/2", "4/5", "1"),
                "filename": f"figure_uc_e0_{e0_filename}.png",
                "title": f"Acceptance ratio versus U_C, E0={e0}",
            },
            {
                "rows": e0_rows, "output": str(output), "x_key": "target_ue",
                "fixed_key": "target_uc", "fixed_values": ("3/10", "1/2", "7/10"),
                "filename": f"figure_ue_e0_{e0_filename}.png",
                "title": f"Acceptance ratio versus U_E, E0={e0}",
            },
        ])
    run_independent_jobs(plot_jobs, _plot_job, workers=args.analysis_workers)
    plot_seconds = time.perf_counter() - plot_started
    invariant["telemetry"] = {
        "validation_summary_seconds": validation_summary_seconds,
        "plot_seconds": plot_seconds,
        "analysis_total_seconds": time.perf_counter() - analysis_started,
        "analysis_workers": args.analysis_workers,
    }
    (output / "invariant_report.json").write_text(json.dumps(invariant, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "analysis_report.json").write_text(json.dumps(invariant, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(invariant, sort_keys=True))
    return 2 if coverage_errors or denominator_violations or dominance_violations or certification_nesting_violations or monotonicity_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
