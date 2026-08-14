#!/usr/bin/env python3
"""Analyze SENS-SMALL coverage, certification, and paired sanity checks."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Mapping

from experiments.v9_3.sens_small import METHODS, SENS_SMALL_PROTOCOL, conditions


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _proven(row: Mapping[str, Any]) -> bool:
    return row.get("final_status") == "PROVEN" or bool(row.get("taskset_proven"))


def _valid_terminal(row: Mapping[str, Any]) -> bool:
    return not bool(row.get("timeout")) and not bool(row.get("error")) and row.get("final_status") not in {
        "UNPROVEN_TIMEOUT", "INTERNAL_ERROR", "NUMERIC_ERROR",
    }


def _dominance_violations(results: list[dict[str, Any]]) -> int:
    groups: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in results:
        if _valid_terminal(row) and _proven(row):
            groups[(str(row.get("skeleton_id")), str(row.get("condition")))][str(row.get("method"))] = row
    violations = 0
    for group in groups.values():
        if not all(method in group for method in METHODS):
            continue
        vectors = {}
        for method in METHODS:
            vector = group[method].get("response_time_vector")
            if not isinstance(vector, list) or not all(isinstance(value, int) for value in vector):
                break
            vectors[method] = vector
        else:
            for weaker, stronger in (("SEQ", "PH"), ("PH", "LOC"), ("LOC", "CW")):
                if any(left > right for left, right in zip(vectors[weaker], vectors[stronger])):
                    violations += 1
    return violations


def _monotonicity_violations(results: list[dict[str, Any]]) -> dict[str, int]:
    by_skeleton: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in results:
        if _valid_terminal(row):
            by_skeleton[(str(row.get("skeleton_id")), str(row.get("method")))][str(row.get("condition"))] = row
    deadline = 0
    latency = 0
    for group in by_skeleton.values():
        if all(name in group for name in ("D_LOW", "CENTER", "D_HIGH")):
            rows = {name: group[name] for name in ("D_LOW", "CENTER", "D_HIGH")}
            if _proven(rows["D_LOW"]) > _proven(rows["CENTER"]) or _proven(rows["CENTER"]) > _proven(rows["D_HIGH"]):
                deadline += 1
        if all(name in group for name in ("L_LOW", "CENTER", "L_HIGH")):
            rows = {name: group[name] for name in ("L_LOW", "CENTER", "L_HIGH")}
            if _proven(rows["L_LOW"]) < _proven(rows["CENTER"]) or _proven(rows["CENTER"]) < _proven(rows["L_HIGH"]):
                latency += 1
    return {"deadline": deadline, "latency": latency}


def analyze(root: Path) -> dict[str, Any]:
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    results = _read_jsonl(root / "results.jsonl")
    expected_rows = list(plan["rows"])
    expected_ids = {str(row["request_id"]) for row in expected_rows}
    observed_ids = [str(row.get("request_id")) for row in results]
    observed_set = set(observed_ids)
    duplicate_ids = len(observed_ids) - len(observed_set)
    missing = expected_ids - observed_set
    unexpected = observed_set - expected_ids
    method_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    condition_groups: dict[str, set[str]] = defaultdict(set)
    for row in expected_rows:
        method_groups[(str(row["skeleton_id"]), str(row["condition"]))].add(str(row["method"]))
        condition_groups[str(row["skeleton_id"])].add(str(row["condition"]))
    partial_method = sum(values != set(METHODS) for values in method_groups.values())
    partial_condition = sum(values != {condition.name for condition in conditions()} for values in condition_groups.values())
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[(str(row.get("target_uc")), str(row.get("axis")), str(row.get("axis_value")), str(row.get("condition")), str(row.get("method")))].append(row)
    summary: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        proven = sum(_proven(row) for row in rows)
        timeout = sum(row.get("final_status") == "UNPROVEN_TIMEOUT" or row.get("solver_status") == "TIMEOUT" for row in rows)
        errors = sum(row.get("final_status") in {"INTERNAL_ERROR", "NUMERIC_ERROR"} or bool(row.get("error")) for row in rows)
        summary.append({
            "U_C": key[0], "axis": key[1], "axis_value": key[2],
            "condition": key[3], "method": key[4],
            "planned_count": sum(
                row["target_uc"] == key[0] and row["axis"] == key[1]
                and row["axis_value"] == key[2] and row["condition"] == key[3]
                and row["method"] == key[4] for row in expected_rows
            ),
            "proven_count": proven,
            "not_proven_count": len(rows) - proven,
            "timeout_count": timeout,
            "error_count": errors,
            "pass_ratio": proven / len(rows) if rows else 0.0,
        })
    with (root / "sens_small_cell_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["U_C", "axis", "axis_value", "condition", "method", "planned_count", "proven_count", "not_proven_count", "timeout_count", "error_count", "pass_ratio"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)
    audit = {
        "protocol": SENS_SMALL_PROTOCOL,
        "plan_identity": plan.get("plan_identity"),
        "expected_request_count": len(expected_ids),
        "observed_request_count": len(results),
        "missing": len(missing),
        "duplicate": duplicate_ids,
        "unexpected": len(unexpected),
        "partial_method_group": partial_method,
        "partial_condition_group": partial_condition,
        "center_generated_once": bool(plan.get("center_generated_once")),
        "dominance_violations": _dominance_violations(results),
        "monotonicity_violations": _monotonicity_violations(results),
        "error_count": sum(row.get("final_status") in {"INTERNAL_ERROR", "NUMERIC_ERROR"} or bool(row.get("error")) for row in results),
        "timeout_count": sum(row.get("final_status") == "UNPROVEN_TIMEOUT" or row.get("solver_status") == "TIMEOUT" for row in results),
    }
    (root / "sens_small_audit.json").write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.input), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
