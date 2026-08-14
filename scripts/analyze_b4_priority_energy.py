#!/usr/bin/env python3
"""Direct B4 result analyzer and pairing/completeness audit."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.b4_priority_energy.experiment import ALGORITHM_CLI, ALGORITHMS  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _trace_statistics(root: Path, result: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "success":
        return {"trace_valid": False}
    trace_path = root / str(result["result_relpath"])
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if (
        trace.get("trace_schema_version") != 3
        or trace.get("run_id") != expected["case_id"]
        or trace.get("configured_scheduler") != ALGORITHM_CLI[expected["algorithm"]]
        or trace.get("expected_simulation_horizon_ms") != 30000
        or trace.get("observed_simulation_end_ms") != 30000
        or trace.get("simulation_completed") is not True
        or trace.get("simulation_completion_reason") != "reached_horizon"
        or trace.get("observability_summary_horizon_ms") != 30000
        or trace.get("taskset_semantic_hash") != expected["taskset_semantic_hash"]
    ):
        raise ValueError(f"trace contract mismatch: {trace_path}")
    tasks = sorted(trace.get("per_task_summary", []), key=lambda row: row["priority_rank"])
    if len(tasks) != 10 or [row["priority_rank"] for row in tasks] != list(range(10)):
        raise ValueError(f"trace task summary mismatch: {trace_path}")
    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        fields = ("released_jobs", "adjudicable_jobs", "completed_jobs", "terminated_jobs", "deadline_miss_jobs", "unfinished_at_horizon_jobs")
        return {field: sum(int(row[field]) for row in rows) for field in fields}
    all_tasks = aggregate(tasks)
    hp_tasks = aggregate(tasks[:4])
    lp_tasks = aggregate(tasks[4:])
    task_pass = [int(row["adjudicable_jobs"]) >= 100 and int(row["deadline_miss_jobs"]) == 0 for row in tasks]
    return {
        "trace_valid": True,
        "whole_pass": all(task_pass),
        "hp_pass": all(task_pass[:4]),
        "lp_pass": all(task_pass[4:]),
        "overall": all_tasks,
        "top4": hp_tasks,
        "bottom6": lp_tasks,
        "energy_summary": trace["energy_summary"],
        "mechanism_summary": trace["mechanism_summary"],
    }


def analyze(root: Path) -> dict[str, Any]:
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    results = _jsonl(root / "results.jsonl")
    expected = {str(row["case_id"]): row for row in plan["rows"]}
    observed_ids = [str(row.get("case_id")) for row in results]
    observed = set(observed_ids)
    duplicate = len(observed_ids) - len(observed)
    missing = set(expected) - observed
    unexpected = observed - set(expected)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[(str(row.get("utilization")), str(row.get("lambda_E")), str(row.get("rho_E")), str(row.get("algorithm")))].append(row)
    cells = []
    for key, rows in sorted(groups.items()):
        stats = [_trace_statistics(root, row, expected[row["case_id"]]) for row in rows]
        successes = [row for row in stats if row.get("trace_valid")]
        overall = [row["overall"] for row in successes]
        cells.append({"utilization": key[0], "lambda_E": key[1], "rho_E": key[2], "algorithm": key[3], "planned": sum(1 for value in expected.values() if (str(value["utilization"]), str(value["lambda_E"]), str(value["rho_E"]), str(value["algorithm"])) == key), "observed": len(rows), "success": sum(row.get("status") == "success" for row in rows), "timeouts": sum(row.get("status") == "timeout" for row in rows), "technical_errors": sum(bool(row.get("technical_error")) for row in rows), "trace_valid": len(successes), "whole_pass": sum(bool(row["whole_pass"]) for row in successes), "hp_pass": sum(bool(row["hp_pass"]) for row in successes), "lp_pass": sum(bool(row["lp_pass"]) for row in successes), "deadline_miss_jobs": sum(row["deadline_miss_jobs"] for row in overall), "unfinished_at_horizon_jobs": sum(row["unfinished_at_horizon_jobs"] for row in overall)})
    with (root / "cell_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["utilization", "lambda_E", "rho_E", "algorithm", "planned", "observed", "success", "timeouts", "technical_errors", "trace_valid", "whole_pass", "hp_pass", "lp_pass", "deadline_miss_jobs", "unfinished_at_horizon_jobs"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cells)
    audit = {
        "experiment": "B4-PE",
        "expected_request_count": len(expected),
        "observed_request_count": len(results),
        "missing": len(missing),
        "duplicate": duplicate,
        "unexpected": len(unexpected),
        "partial_scheduler_groups": sum(row["planned"] != row["observed"] for row in cells),
        "technical_error": sum(bool(row.get("technical_error")) for row in results),
        "timeout": sum(row.get("status") == "timeout" for row in results),
        "scheduler_ids": list(ALGORITHMS),
        "pairing_complete": duplicate == 0 and not missing and not unexpected,
    }
    (root / "b4_audit.json").write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.input), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
