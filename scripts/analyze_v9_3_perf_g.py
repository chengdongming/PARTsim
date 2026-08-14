#!/usr/bin/env python3
"""Analyze PERF-G results and perform Q-only calibration selection."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.v9_3 import perf_g
from experiments.v9_3.performance_outcome import evaluate_outcome


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    columns = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def completeness(requests: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]], schedulers: Sequence[str]) -> dict[str, Any]:
    expected = {str(row["request_id"]) for row in requests}
    observed = [str(row.get("request_id")) for row in results]
    counts = {request: observed.count(request) for request in set(observed)}
    duplicates = sorted(request for request, count in counts.items() if count > 1)
    missing = sorted(expected - set(observed))
    unexpected = sorted(set(observed) - expected)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in results:
        groups.setdefault((str(row.get("taskset_id")), str(row.get("energy_condition"))), []).append(row)
    expected_scheduler_set = set(schedulers)
    partial = [
        {"taskset_id": key[0], "energy_condition": key[1],
         "observed_schedulers": sorted(str(row.get("scheduler")) for row in rows)}
        for key, rows in sorted(groups.items())
        if len(rows) != len(expected_scheduler_set)
        or {str(row.get("scheduler")) for row in rows} != expected_scheduler_set
    ]
    return {
        "missing": len(missing), "missing_request_ids": missing,
        "duplicate": len(duplicates), "duplicate_request_ids": duplicates,
        "unexpected": len(unexpected), "unexpected_request_ids": unexpected,
        "partial_group": len(partial), "partial_groups": partial,
    }


def confirmation_status(selection: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> bool:
    q = perf_g.q_matrix(rows)
    kappa = str(selection["kappa_star"])
    low = selection["eta_low"]
    transition = selection["eta_transition"]
    high = selection["eta_high"]
    transition_values = [
        q.get((kappa, transition, str(utilization)), 0.0)
        for utilization in perf_g.CAL_UTILIZATIONS
    ]
    return (
        q.get((kappa, low, "1/2"), 1.0) <= 0.2
        and sum(0.2 <= value <= 0.8 for value in transition_values) >= 2
        and q.get((kappa, high, "1/2"), 0.0) >= 0.8
    )


def select_calibration(
    initial_rows: Sequence[Mapping[str, Any]],
    *,
    extension_rows: Sequence[Mapping[str, Any]] = (),
    confirmation_rows: Sequence[Mapping[str, Any]] = (),
    fallback_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return the Q-only selection state, including the one allowed extension."""

    initial = perf_g.select_three_conditions(initial_rows)
    if initial is None:
        transition = perf_g.select_transition(perf_g.q_matrix(initial_rows))
        extension_kind = "A" if transition is not None else "B"
        combined = list(initial_rows) + list(extension_rows)
        selected = perf_g.select_three_conditions(combined)
        if selected is None:
            return {
                "status": f"NEEDS_EXTENSION_{extension_kind}" if not extension_rows else "CAL_BLOCKED",
                "selection": None,
                "required_extension": extension_kind,
            }
        initial = selected
        selection_status = f"EXTENSION_{extension_kind}_APPLIED"
    else:
        selection_status = "INITIAL_GRID"

    if confirmation_rows:
        if not confirmation_status(initial, confirmation_rows):
            if fallback_rows:
                fallback = perf_g.select_three_conditions(fallback_rows)
                if fallback is not None and confirmation_status(fallback, confirmation_rows):
                    initial = fallback
                    selection_status = "FULL_GRID_FALLBACK"
                else:
                    return {"status": "CAL_BLOCKED", "selection": None, "required_extension": None}
            else:
                return {"status": "NEEDS_FULL_GRID_FALLBACK", "selection": initial, "required_extension": None}
    return {"status": selection_status + "_CONFIRMED" if confirmation_rows else selection_status,
            "selection": initial, "required_extension": None}


def analyze_calibration(root: Path) -> dict[str, Any]:
    requests = _read_jsonl(root / "requests.jsonl")
    results = _read_jsonl(root / "results.jsonl")
    checks = completeness(requests, results, perf_g.CAL_SCHEDULERS)
    selection = select_calibration(results)
    document = {
        "kind": "CAL", "completeness": checks,
        "selection_status": selection["status"], "selection": selection.get("selection"),
        "q": {"|".join(key): value for key, value in perf_g.q_matrix(results).items()},
    }
    if selection.get("selection") is not None:
        _write_json(root / "calibration_selection.json", selection["selection"])
    _write_json(root / "calibration_analysis.json", document)
    return document


def analyze_results(root: Path, mode: str) -> dict[str, Any]:
    requests = _read_jsonl(root / "requests.jsonl")
    results = _read_jsonl(root / "results.jsonl")
    schedulers = perf_g.CAL_SCHEDULERS if mode == "CAL" else perf_g.FORMAL_SCHEDULERS
    checks = completeness(requests, results, schedulers)
    cell_values: dict[tuple[str, str, str], list[bool]] = {}
    secondary: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in results:
        outcome = row.get("outcome", {})
        jobs = row.get("jobs", [])
        task_ids = sorted({str(job.get("task_id")) for job in jobs})
        recomputed = evaluate_outcome(
            jobs, task_ids, horizon=int(row.get("horizon_ms", perf_g.FORMAL_HORIZON_MS)),
            minimum_adjudicable_jobs=1 if mode != "FORMAL" else perf_g.FORMAL_MIN_ADJUDICABLE_JOBS,
            simulation_completed=row.get("simulation_status") not in {"TECHNICAL_FAILURE", "RUNTIME_TIMEOUT", "INTERNAL_ERROR"},
            technical_error=row.get("technical_error"),
        )
        if outcome and recomputed.get("taskset_pass") != outcome.get("taskset_pass"):
            raise ValueError(f"outcome mismatch for {row.get('request_id')}")
        key = (str(row.get("U_norm")), str(row.get("energy_condition")), str(row.get("scheduler")))
        if recomputed.get("taskset_pass") is not None:
            cell_values.setdefault(key, []).append(bool(recomputed["taskset_pass"]))
        metrics = row.get("metrics", {})
        bucket = secondary.setdefault(key, {"energy_blocked_ticks": 0.0, "harvested_energy_j": 0.0, "consumed_energy_j": 0.0})
        for field in bucket:
            value = metrics.get(field)
            if isinstance(value, (int, float)):
                bucket[field] += float(value)
    cell_rows = []
    for key, values in sorted(cell_values.items()):
        cell_rows.append({
            "U_norm": key[0], "energy_condition": key[1], "scheduler": key[2],
            "pass_ratio": sum(values) / len(values), "available_tasksets": len(values),
        })
    secondary_rows = [
        {"U_norm": key[0], "energy_condition": key[1], "scheduler": key[2], **values}
        for key, values in sorted(secondary.items())
    ]
    _write_csv(root / "cell_summary.csv", cell_rows)
    _write_csv(root / "secondary_metrics.csv", secondary_rows)
    document = {"kind": mode, "completeness": checks, "cell_summary": cell_rows,
                "secondary_metrics": secondary_rows}
    _write_json(root / "analysis.json", document)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mode", choices=("CAL", "FORMAL", "SMOKE"), required=True)
    args = parser.parse_args(argv)
    if args.mode == "CAL":
        document = analyze_calibration(args.input)
    else:
        document = analyze_results(args.input, args.mode)
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["analyze_calibration", "analyze_results", "completeness", "confirmation_status", "select_calibration"]
