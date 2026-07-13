#!/usr/bin/env python3
"""Validate and summarize ASAP-BLOCK v9.3 five-variant pilot outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from asap_block_v1_3_12_schema_binding import V1312SchemaBinding


SUMMARY_COLUMNS = (
    "U_norm", "E0", "analysis_variant", "requested_tasksets",
    "completed_analyses", "certified_tasksets", "not_certified",
    "not_applicable", "no_candidate", "timeout", "numeric_error",
    "internal_conformance_failure", "certification_ratio", "mean_runtime",
    "median_runtime", "p95_runtime", "max_runtime", "mean_candidate",
    "dominance_common_count", "dominance_tighter_count",
    "dominance_equal_count", "dominance_violation_count",
)
RUNTIME_COLUMNS = (
    "taskset_id", "U_norm", "E0", "analysis_variant", "analysis_id",
    "solver_status", "certification_status", "wall_clock_runtime_seconds",
    "cpu_runtime_seconds", "timeout", "numeric_error",
    "internal_conformance_failure",
)


class AnalysisError(RuntimeError):
    """Raised when pilot output cannot be summarized safely."""


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisError("non-finite numeric output")
    return result


def _p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _runtime_metrics(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "max": None}
    return {
        "mean": statistics.fmean(values), "median": statistics.median(values),
        "p95": _p95(values), "max": max(values),
    }


def _validate_canonical_projections(root: Path) -> int:
    binding = V1312SchemaBinding()
    failures = 0
    for table_name in (
        "per_taskset_results.csv", "per_task_results.csv", "rta_dependency_records.csv"
    ):
        path = root / "canonical_v1_3_12" / table_name
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != binding.canonical_columns(table_name):
                    raise AnalysisError("noncanonical header for {}".format(table_name))
                for row in reader:
                    binding.validate_encoded_row(table_name, row)
        except Exception:
            failures += 1
    return failures


def _dominance_by_group(rows: Sequence[Mapping[str, str]]) -> Dict[Tuple[str, str, str], Counter]:
    variant_by_relation = {
        "DEADLINE_CARRY_IN": "LOC_D",
        "FIXED_CW_CARRY_IN": "LOC_THETA_CW",
        "RECURSIVE_CARRY_IN": "LOC_THETA_LOC",
    }
    result: Dict[Tuple[str, str, str], Counter] = defaultdict(Counter)
    for row in rows:
        variant = variant_by_relation.get(row["relation"])
        if variant is None or row["status"] == "NOT_APPLICABLE":
            continue
        counter = result[(row["U_norm"], row["E0"], variant)]
        counter["common"] += 1
        counter[row["status"].lower()] += 1
    return result


def _summary_rows(
    tasksets: Sequence[Mapping[str, str]], tasks: Sequence[Mapping[str, str]],
    dominance: Sequence[Mapping[str, str]],
) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str, str], List[Mapping[str, str]]] = defaultdict(list)
    candidate_groups: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    for row in tasksets:
        grouped[(row["U_norm"], row["E0"], row["analysis_variant"])].append(row)
    for row in tasks:
        if row["candidate_response_time"]:
            candidate_groups[(row["U_norm"], row["E0"], row["analysis_variant"])].append(
                _float(row["candidate_response_time"])
            )
    dominance_groups = _dominance_by_group(dominance)
    rows = []
    for key in sorted(grouped, key=lambda item: (float(item[0]), int(item[1]), item[2])):
        group = grouped[key]
        runtimes = [_float(row["wall_clock_runtime_seconds"]) for row in group]
        runtime = _runtime_metrics(runtimes)
        certified = sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in group)
        requested = len(group)
        dcounts = dominance_groups[key]
        candidates = candidate_groups.get(key, [])
        rows.append({
            "U_norm": key[0], "E0": key[1], "analysis_variant": key[2],
            "requested_tasksets": requested,
            "completed_analyses": sum(row["solver_status"] == "COMPLETED" for row in group),
            "certified_tasksets": certified,
            "not_certified": sum(row["certification_status"] == "NOT_CERTIFIED" for row in group),
            "not_applicable": sum(row["certification_status"] == "NOT_APPLICABLE" for row in group),
            "no_candidate": sum(row["solver_status"] == "NO_CANDIDATE" for row in group),
            "timeout": sum(_truthy(row["timeout"]) for row in group),
            "numeric_error": sum(_truthy(row["numeric_error"]) for row in group),
            "internal_conformance_failure": sum(
                _truthy(row["internal_conformance_failure"]) for row in group
            ),
            "certification_ratio": certified / requested if requested else None,
            "mean_runtime": runtime["mean"], "median_runtime": runtime["median"],
            "p95_runtime": runtime["p95"], "max_runtime": runtime["max"],
            "mean_candidate": statistics.fmean(candidates) if candidates else None,
            "dominance_common_count": dcounts["common"],
            "dominance_tighter_count": dcounts["tighter"],
            "dominance_equal_count": dcounts["equal"],
            "dominance_violation_count": dcounts["violation"],
        })
    return rows


def _dependency_hash_failures(tasksets: Sequence[Mapping[str, str]]) -> int:
    failures = 0
    for row in tasksets:
        if row["analysis_variant"] != "LOC_THETA_CW":
            continue
        certified = row["certification_status"] == "CERTIFIED_TASKSET"
        hashes_match = bool(row["source_vector_hash"]) and (
            row["source_vector_hash"] == row["target_carry_in_vector_hash"]
        )
        if certified and (row["dependency_status"] != "VALID" or not hashes_match):
            failures += 1
    return failures


def _missing_and_duplicate(
    generated: Sequence[Mapping[str, str]], tasksets: Sequence[Mapping[str, str]]
) -> Tuple[int, int]:
    expected_variants = {
        "CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC"
    }
    counts = Counter((row["taskset_id"], row["analysis_variant"]) for row in tasksets)
    duplicates = sum(value - 1 for value in counts.values() if value > 1)
    missing = 0
    for generated_row in generated:
        present = {
            variant for (taskset_id, variant), value in counts.items()
            if taskset_id == generated_row["taskset_id"] and value
        }
        missing += len(expected_variants - present)
    return missing, duplicates


def _runtime_rows(tasksets: Sequence[Mapping[str, str]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column) for column in RUNTIME_COLUMNS} for row in tasksets]


def _problem_levels(summary: Mapping[str, Any]) -> Dict[str, List[str]]:
    p0 = []
    for key, label in (
        ("generation_failures", "generation failure"),
        ("missing_results", "missing analysis result"),
        ("duplicate_results", "duplicate analysis result"),
        ("schema_serialization_failures", "schema/serialization failure"),
        ("dependency_hash_failures", "LOC_THETA_CW dependency/hash failure"),
        ("dominance_violations", "dominance violation"),
        ("crashes", "crash or unhandled worker exception"),
        ("illegal_service_curves", "illegal service curve"),
    ):
        if summary[key]:
            p0.append("{}: {}".format(label, summary[key]))
    p1 = []
    for key, label in (
        ("timeouts", "analysis timeouts"),
        ("numeric_errors", "numeric errors"),
        ("internal_conformance_failures", "internal conformance failures"),
    ):
        if summary[key]:
            p1.append("{}: {}".format(label, summary[key]))
    p2 = []
    if summary["certified_tasksets"] == 0:
        p2.append("all variants have zero certified tasksets; parameter grid lacks usable certification coverage")
    return {"P0": p0, "P1": p1, "P2": p2}


def _report(summary: Mapping[str, Any], groups: Sequence[Mapping[str, Any]]) -> str:
    runtime = summary["runtime"]
    problems = summary["problems"]
    lines = [
        "# ASAP-BLOCK v9.3 five-variant pilot report", "",
        "This is a pipeline pilot, not a formal paper experiment or final statistical conclusion.", "",
        "## Outcome", "",
        "- Mode: `{}`".format(summary["mode"]),
        "- Pipeline passed: `{}`".format(str(summary["pipeline_passed"]).lower()),
        "- Generated tasksets: `{}/{}`".format(summary["generated_tasksets"], summary["expected_tasksets"]),
        "- Analysis terminal rows: `{}/{}`".format(summary["analysis_results"], summary["expected_analyses"]),
        "- Certified tasksets (analysis rows): `{}`".format(summary["certified_tasksets"]),
        "- Certified rows with E0=1: `{}`".format(summary["e0_1_certified_tasksets"]),
        "- Missing / duplicate / schema failures: `{}` / `{}` / `{}`".format(
            summary["missing_results"], summary["duplicate_results"],
            summary["schema_serialization_failures"],
        ),
        "", "## Runtime and failures", "",
        "- Runtime mean / median / p95 / max: `{:.6f}` / `{:.6f}` / `{:.6f}` / `{:.6f}` seconds".format(
            runtime["mean"] or 0, runtime["median"] or 0,
            runtime["p95"] or 0, runtime["max"] or 0,
        ),
        "- Timeout / numeric / internal: `{}` / `{}` / `{}`".format(
            summary["timeouts"], summary["numeric_errors"],
            summary["internal_conformance_failures"],
        ),
        "- Generation / crash / illegal service curve: `{}` / `{}` / `{}`".format(
            summary["generation_failures"], summary["crashes"],
            summary["illegal_service_curves"],
        ),
        "", "## Dominance", "",
    ]
    for relation, values in summary["dominance"].items():
        lines.append(
            "- {}: common `{}`, tighter `{}`, equal `{}`, violations `{}`, max improvement `{}`, mean improvement `{}`".format(
                relation, values["common"], values["tighter"], values["equal"],
                values["violation"], values["maximum_improvement"], values["mean_improvement"],
            )
        )
    lines.extend(["", "## Certification ratio by cell and variant", ""])
    for row in groups:
        lines.append(
            "- U_norm={}, E0={}, {}: {}/{} = {:.3f}".format(
                row["U_norm"], row["E0"], row["analysis_variant"],
                row["certified_tasksets"], row["requested_tasksets"],
                row["certification_ratio"] or 0,
            )
        )
    lines.extend(["", "## Initial E0 comparison", ""])
    for e0, values in summary["e0_comparison"].items():
        lines.append(
            "- E0={}: certified `{}/{}` ({:.3f}), mean runtime `{:.6f}` seconds".format(
                e0, values["certified"], values["requested"],
                values["certification_ratio"], values["mean_runtime"],
            )
        )
    lines.extend(["", "## Problems", ""])
    for level in ("P0", "P1", "P2"):
        entries = problems[level]
        lines.append("- {}: {}".format(level, "; ".join(entries) if entries else "none"))
    if summary.get("smoke_reproducibility") is not None:
        lines.extend([
            "", "## Smoke reproducibility", "",
            "- Same-seed semantic and non-runtime outcomes match: `{}`".format(
                str(summary["smoke_reproducibility"]["match"]).lower()
            ),
        ])
    lines.extend([
        "", "## Next step", "",
        "The formal pilot parameter expansion may be designed only if this main pipeline is marked passed; these rows are not final paper statistics.",
        "",
    ])
    return "\n".join(lines)


def _dominance_overall(rows: Sequence[Mapping[str, str]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        if row["status"] != "NOT_APPLICABLE":
            grouped[row["relation"]].append(row)
    result = {}
    for relation in ("DEADLINE_CARRY_IN", "FIXED_CW_CARRY_IN", "RECURSIVE_CARRY_IN"):
        group = grouped.get(relation, [])
        improvements = [int(row["improvement"]) for row in group]
        result[relation] = {
            "common": len(group),
            "tighter": sum(row["status"] == "TIGHTER" for row in group),
            "equal": sum(row["status"] == "EQUAL" for row in group),
            "violation": sum(row["status"] == "VIOLATION" for row in group),
            "maximum_improvement": max(improvements) if improvements else None,
            "mean_improvement": statistics.fmean(improvements) if improvements else None,
        }
    return result


def _e0_comparison(tasksets: Sequence[Mapping[str, str]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    for row in tasksets:
        grouped[row["E0"]].append(row)
    result = {}
    for e0, rows in sorted(grouped.items(), key=lambda item: int(item[0])):
        certified = sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in rows)
        runtimes = [_float(row["wall_clock_runtime_seconds"]) for row in rows]
        result[e0] = {
            "requested": len(rows), "certified": certified,
            "certification_ratio": certified / len(rows) if rows else 0,
            "mean_runtime": statistics.fmean(runtimes) if runtimes else 0,
        }
    return result


def _hash_files(root: Path) -> None:
    target = root / "file_hashes.sha256"
    lines = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path == target:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append("{}  {}".format(digest, path.relative_to(root).as_posix()))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def finalize_pilot_outputs(root: Path, *, mode: str, hard_failure: bool = False) -> Dict[str, Any]:
    root = Path(root)
    generated = _read_csv(root / "generated_tasksets.csv")
    tasksets = _read_csv(root / "per_taskset_results.csv")
    tasks = _read_csv(root / "per_task_results.csv")
    dominance_rows = _read_csv(root / "dominance_checks.csv")
    failures = _read_csv(root / "failures.csv")
    groups = _summary_rows(tasksets, tasks, dominance_rows)
    _write_csv(root / "pilot_summary.csv", SUMMARY_COLUMNS, groups)
    _write_csv(root / "pilot_runtime.csv", RUNTIME_COLUMNS, _runtime_rows(tasksets))
    missing, duplicates = _missing_and_duplicate(generated, tasksets)
    schema_failures = _validate_canonical_projections(root)
    canonical_tasksets = _read_csv(root / "canonical_v1_3_12" / "per_taskset_results.csv")
    if len(canonical_tasksets) != len(tasksets):
        schema_failures += abs(len(canonical_tasksets) - len(tasksets))
    expected_tasksets = 12 if mode == "smoke" else 60
    expected_analyses = expected_tasksets * 5
    runtimes = [_float(row["wall_clock_runtime_seconds"]) for row in tasksets]
    runtime = _runtime_metrics(runtimes)
    dominance = _dominance_overall(dominance_rows)
    summary: Dict[str, Any] = {
        "mode": mode, "expected_tasksets": expected_tasksets,
        "expected_analyses": expected_analyses,
        "generated_tasksets": len(generated), "analysis_results": len(tasksets),
        "per_task_results": len(tasks),
        "certified_tasksets": sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in tasksets),
        "e0_1_certified_tasksets": sum(
            row["E0"] == "1" and row["certification_status"] == "CERTIFIED_TASKSET"
            for row in tasksets
        ),
        "generation_failures": sum(row.get("stage") == "generation" for row in failures),
        "missing_results": missing, "duplicate_results": duplicates,
        "schema_serialization_failures": schema_failures,
        "dependency_hash_failures": _dependency_hash_failures(tasksets),
        "illegal_service_curves": sum(row["service_curve_status"] != "VALID" for row in tasksets),
        "crashes": len(failures),
        "timeouts": sum(_truthy(row["timeout"]) for row in tasksets),
        "numeric_errors": sum(_truthy(row["numeric_error"]) for row in tasksets),
        "internal_conformance_failures": sum(
            _truthy(row["internal_conformance_failure"]) for row in tasksets
        ),
        "dominance_violations": sum(values["violation"] for values in dominance.values()),
        "runtime": runtime, "dominance": dominance,
        "e0_comparison": _e0_comparison(tasksets), "groups": groups,
        "smoke_reproducibility": None,
    }
    summary["pipeline_passed"] = bool(
        not hard_failure
        and summary["generated_tasksets"] == expected_tasksets
        and summary["analysis_results"] == expected_analyses
        and summary["missing_results"] == 0
        and summary["duplicate_results"] == 0
        and summary["schema_serialization_failures"] == 0
        and summary["dependency_hash_failures"] == 0
        and summary["illegal_service_curves"] == 0
        and summary["crashes"] == 0
        and summary["dominance_violations"] == 0
        and summary["certified_tasksets"] > 0
        and summary["e0_1_certified_tasksets"] > 0
        and len(runtimes) == expected_analyses
    )
    summary["problems"] = _problem_levels(summary)
    _write_json(root / "pilot_summary.json", summary)
    (root / "pilot_report.md").write_text(_report(summary, groups), encoding="utf-8")
    _hash_files(root)
    return summary


def _normalized_rows(root: Path, filename: str, excluded: Sequence[str]) -> List[Dict[str, str]]:
    rows = _read_csv(root / filename)
    normalized = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in excluded}
        if filename == "per_task_results.csv" and row.get("task_solver_status") == "TIMEOUT":
            # A wall-clock cutoff makes the final visit counts operationally
            # nondeterministic by a few loop iterations.  The timeout task,
            # completed prefix, solver/certification state, and failure code
            # remain part of the reproducibility comparison.
            for field in (
                "checked_w_count", "checked_h_count", "checked_q_count",
                "envelope_calls",
            ):
                item[field] = "TIMEOUT_CUTOFF_DEPENDENT"
        normalized.append(item)
    return sorted(
        normalized,
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
    )


def compare_smoke_roots(left: Path, right: Path) -> Dict[str, Any]:
    comparisons = {}
    specs = {
        "generated_tasksets.csv": ("generation_runtime_seconds",),
        "per_taskset_results.csv": (
            "wall_clock_runtime_seconds", "cpu_runtime_seconds",
        ),
        "per_task_results.csv": (),
        "dominance_checks.csv": (),
    }
    for filename, excluded in specs.items():
        comparisons[filename] = (
            _normalized_rows(Path(left), filename, excluded)
            == _normalized_rows(Path(right), filename, excluded)
        )
    return {"match": all(comparisons.values()), "files": comparisons}


def attach_smoke_reproducibility(root: Path, comparison: Mapping[str, Any]) -> Dict[str, Any]:
    root = Path(root)
    _write_json(root / "smoke_reproducibility.json", comparison)
    summary = json.loads((root / "pilot_summary.json").read_text(encoding="utf-8"))
    summary["smoke_reproducibility"] = dict(comparison)
    if not comparison.get("match"):
        summary["pipeline_passed"] = False
        summary["problems"]["P0"].append("same-seed smoke outcomes are not reproducible")
    _write_json(root / "pilot_summary.json", summary)
    (root / "pilot_report.md").write_text(_report(summary, summary["groups"]), encoding="utf-8")
    _hash_files(root)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    parser.add_argument("--compare-root", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = finalize_pilot_outputs(args.output_root, mode=args.mode)
        if args.compare_root is not None:
            comparison = compare_smoke_roots(args.output_root, args.compare_root)
            summary = attach_smoke_reproducibility(args.output_root, comparison)
    except Exception as exc:
        print("v9.3 pilot analysis failed: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["pipeline_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
