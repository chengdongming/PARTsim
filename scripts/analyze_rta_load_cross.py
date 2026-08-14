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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.rta_load_cross import fraction_text  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze RTA-LOAD-CROSS JSONL output.")
    parser.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    output = Path(args.input)
    tasksets = _read_jsonl(output / "tasksets.jsonl")
    results = _read_jsonl(output / "results.jsonl")
    if len({row.get("request_id") for row in results}) != len(results):
        print("duplicate request_id in results.jsonl", file=sys.stderr)
        return 2
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
        counts = [len(groups.get((*cell, method), ())) for method in ("CW", "LOC", "PH", "SEQ") if (*cell, method) in groups]
        if counts and len(set(counts)) != 1:
            denominator_violations += 1
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = list(summary[0]) if summary else ["target_uc", "target_ue", "e0", "method", "n_total", "n_proven", "acceptance_ratio", "n_timeout", "timeout_ratio", "n_no_candidate", "n_numeric_error", "n_internal_error"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)

    dominance_violations = 0
    certification_nesting_violations = 0
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
        for stronger, weaker in (("CW", "LOC"), ("LOC", "PH"), ("PH", "SEQ")):
            certification_nesting_violations += len(proven[stronger] - proven[weaker])
    monotonicity_violations = 0
    for taskset_id in {row["taskset_id"] for row in results}:
        for method in {row["method"] for row in results}:
            zero = next((row for row in results if row["taskset_id"] == taskset_id and row["method"] == method and row["e0"] == "0"), None)
            high = next((row for row in results if row["taskset_id"] == taskset_id and row["method"] == method and row["e0"] == "37"), None)
            if zero and high and zero.get("final_status") == "PROVEN" and high.get("final_status") != "PROVEN":
                monotonicity_violations += 1
            if zero and high and zero.get("response_time_vector") and high.get("response_time_vector"):
                if any(a is not None and b is not None and b > a for a, b in zip(zero["response_time_vector"], high["response_time_vector"])):
                    monotonicity_violations += 1
    invariant = {"dominance_violation_count": dominance_violations, "certification_nesting_violation_count": certification_nesting_violations, "e0_monotonicity_violation_count": monotonicity_violations, "denominator_violation_count": denominator_violations, "n_tasksets": len(tasksets), "n_results": len(results)}
    (output / "invariant_report.json").write_text(json.dumps(invariant, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    e0_zero = [row for row in summary if row["e0"] == "0"]
    _plot(e0_zero, output, x_key="target_uc", fixed_key="target_ue", fixed_values=("1/2", "4/5", "1"), filename="figure_uc_e0_0.png", title="Acceptance ratio versus U_C, E0=0")
    _plot(e0_zero, output, x_key="target_ue", fixed_key="target_uc", fixed_values=("3/10", "1/2", "7/10"), filename="figure_ue_e0_0.png", title="Acceptance ratio versus U_E, E0=0")
    _plot([row for row in summary if row["e0"] == "37"], output, x_key="target_uc", fixed_key="target_ue", fixed_values=("1/2", "4/5", "1"), filename="figure_uc_e0_37.png", title="Acceptance ratio versus U_C, E0=37")
    _plot([row for row in summary if row["e0"] == "37"], output, x_key="target_ue", fixed_key="target_uc", fixed_values=("3/10", "1/2", "7/10"), filename="figure_ue_e0_37.png", title="Acceptance ratio versus U_E, E0=37")
    print(json.dumps(invariant, sort_keys=True))
    return 2 if dominance_violations or certification_nesting_violations or monotonicity_violations or denominator_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
