#!/usr/bin/env python3
"""Summarize ASAP-BLOCK v9.3 Pilot-3 exact-E0 paired screening."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


class AnalysisError(RuntimeError):
    """Pilot-3 artifact analysis failure."""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def classify_cell(metrics: Mapping[str, int]) -> str:
    """Return the strongest predeclared A--E outcome for one paired cell."""

    if int(metrics.get("certification_gain_tasksets", 0)):
        return "E_CERTIFICATION_GAIN"
    if int(metrics.get("response_strict_tasks", 0)):
        return "D_RESPONSE_STRICT"
    if int(metrics.get("local_only_closures", 0)):
        return "C_LOCAL_ONLY_CLOSURE_CANDIDATE_EQUAL"
    if int(metrics.get("envelope_strict_accesses", 0)):
        return "B_ENVELOPE_STRICT_NO_LOCAL_ONLY_CLOSURE"
    return "A_NO_ENVELOPE_STRICTNESS"


def _timing(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Optional[float]]:
    values = [float(row["total_wall_seconds"]) for row in rows]
    if not values:
        return {"mean": None, "median": None, "max": None}
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def summarize_interim(
    results: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
    access_rows: Sequence[Mapping[str, Any]], closure_rows: Sequence[Mapping[str, Any]],
    response_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Summarize a deliberately paused or adaptively truncated Pilot-3 run."""

    variants = ("CW_D", "LOC_D", "CW_THETA_CW", "LOC_THETA_CW", "LOC_THETA_LOC")
    grouped: Dict[tuple, list] = defaultdict(list)
    for row in results:
        grouped[(str(row["taskset_id"]), str(row["exact_e0"]))].append(row)
    incomplete = {
        f"{taskset_id}|{e0}": sorted(str(row["analysis_variant"]) for row in rows)
        for (taskset_id, e0), rows in grouped.items()
        if len(rows) != len(variants) or {str(row["analysis_variant"]) for row in rows} != set(variants)
    }
    if incomplete:
        raise AnalysisError(f"screening_results contains incomplete instances: {incomplete}")
    result_ids = {str(row["analysis_id"]) for row in results}
    orphan_attempts = [row for row in attempts if str(row["analysis_id"]) not in result_ids]

    status_by_cell_variant: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for row in results:
        cell = f"U{row['U_norm']}-E0={row['exact_e0']}"
        status_by_cell_variant[cell][str(row["analysis_variant"])][str(row["final_solver_status"])] += 1
    relation_summary = {}
    for relation in ("DEADLINE_CARRY_IN", "FIXED_CW_CARRY_IN", "RECURSIVE_CARRY_IN"):
        rows = [row for row in response_rows if row["relation"] == relation]
        relation_summary[relation] = {
            "common_candidates": sum(row["response_relation"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in rows),
            "strict_response_tasks": sum(row["response_relation"] == "TIGHTER" for row in rows),
            "equal_tasks": sum(row["response_relation"] == "EQUAL" for row in rows),
            "violations": sum(row["response_relation"] == "VIOLATION" for row in rows),
            "certification_gain_tasks": sum(_truthy(row["certification_gain"]) for row in rows),
        }
    strict_response = sum(item["strict_response_tasks"] for item in relation_summary.values())
    certification_gain = sum(item["certification_gain_tasks"] for item in relation_summary.values())
    cell_results: Dict[tuple, list] = defaultdict(list)
    cell_access: Dict[tuple, list] = defaultdict(list)
    cell_closure: Dict[tuple, list] = defaultdict(list)
    cell_response: Dict[tuple, list] = defaultdict(list)
    for row in results:
        cell_results[(str(row["U_norm"]), str(row["exact_e0"]))].append(row)
    for collection, destination in (
        (access_rows, cell_access), (closure_rows, cell_closure),
        (response_rows, cell_response),
    ):
        for row in collection:
            destination[(str(row["U_norm"]), str(row["exact_e0"]))].append(row)
    cells = {}
    relation_pairs = {
        "DEADLINE_CARRY_IN": ("CW_D", "LOC_D"),
        "FIXED_CW_CARRY_IN": ("CW_THETA_CW", "LOC_THETA_CW"),
        "RECURSIVE_CARRY_IN": ("CW_THETA_CW", "LOC_THETA_LOC"),
    }
    for key, rows in sorted(cell_results.items()):
        u_norm, e0 = key
        taskset_ids = {str(row["taskset_id"]) for row in rows}
        lookup = {
            (str(row["taskset_id"]), str(row["analysis_variant"])): row for row in rows
        }
        gain_tasksets = set()
        for relation, (complete_variant, local_variant) in relation_pairs.items():
            for taskset_id in taskset_ids:
                complete = lookup[(taskset_id, complete_variant)]
                local = lookup[(taskset_id, local_variant)]
                if (
                    local["certification_status"] == "CERTIFIED_TASKSET"
                    and complete["certification_status"] != "CERTIFIED_TASKSET"
                ):
                    gain_tasksets.add((taskset_id, relation))
        access = cell_access[key]
        closures = cell_closure[key]
        responses = cell_response[key]
        metrics = {
            "envelope_strict_accesses": sum(row["envelope_relation"] == "STRICT" for row in access),
            "local_only_closures": len(closures),
            "response_common_candidate_tasks": sum(row["response_relation"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in responses),
            "response_strict_tasks": sum(row["response_relation"] == "TIGHTER" for row in responses),
            "response_strict_tasksets": len({str(row["taskset_id"]) for row in responses if row["response_relation"] == "TIGHTER"}),
            "response_equal_tasks": sum(row["response_relation"] == "EQUAL" for row in responses),
            "response_violations": sum(row["response_relation"] == "VIOLATION" for row in responses),
            "certification_gain_tasksets": len({item[0] for item in gain_tasksets}),
            "certification_gain_tasks": sum(_truthy(row["certification_gain"]) for row in responses),
        }
        cell_id = f"U{u_norm}-E0={e0}"
        cells[cell_id] = {
            "U_norm": u_norm, "exact_e0": e0,
            "sampled_tasksets": len(taskset_ids), "analyses": len(rows),
            "sample_scope": "FULL_FIVE" if len(taskset_ids) == 5 else "ADAPTIVE_GATE_TWO",
            "class": classify_cell(metrics),
            "final_statuses": dict(sorted(Counter(row["final_solver_status"] for row in rows).items())),
            "timeouts_after_retry": sum(row["final_solver_status"] == "TIMEOUT" for row in rows),
            **metrics,
        }
    initial_timeouts = sum(
        int(row["attempt_budget_seconds"]) == 60 and row["solver_status"] == "TIMEOUT"
        for row in attempts
    )
    retries = [row for row in attempts if int(row["attempt_budget_seconds"]) == 90]
    return {
        "run_state": "PAUSED_ADAPTIVE_SCREENING",
        "completed_taskset_e0_instances": len(grouped),
        "completed_analyses": len(results),
        "complete_instance_invariant": not incomplete,
        "status_by_U_E0_variant": {
            cell: {variant: dict(sorted(statuses.items())) for variant, statuses in sorted(values.items())}
            for cell, values in sorted(status_by_cell_variant.items())
        },
        "signals": {
            "envelope_accesses": len(access_rows),
            "strict_envelope_accesses": sum(row["envelope_relation"] == "STRICT" for row in access_rows),
            "predicted_energy_separation_hits": sum(_truthy(row["predicted_interval_hit"]) for row in access_rows),
            "local_only_closures": len(closure_rows),
            "strict_response_tasks": strict_response,
            "certification_gain_tasks": certification_gain,
            "relations": relation_summary,
        },
        "cells": cells,
        "timeouts": {
            "initial_60_second_timeouts": initial_timeouts,
            "conditional_90_second_attempts": len(retries),
            "resolved_at_90": sum(row["solver_status"] != "TIMEOUT" for row in retries),
            "timeout_at_90": sum(row["solver_status"] == "TIMEOUT" for row in retries),
            "final_timeout_analyses": sum(row["final_solver_status"] == "TIMEOUT" for row in results),
        },
        "orphan_attempts_preserved": [
            {
                key: row[key] for key in (
                    "taskset_id", "U_norm", "exact_e0", "analysis_variant",
                    "attempt_budget_seconds", "solver_status", "analysis_id",
                )
            } for row in orphan_attempts
        ],
        "dominance": {
            "envelope_violations": sum(row["envelope_relation"] == "VIOLATION" for row in access_rows),
            "response_violations": sum(item["violations"] for item in relation_summary.values()),
        },
    }


def write_interim_report(root: Path, summary: Mapping[str, Any]) -> None:
    signals = summary["signals"]
    timeout = summary["timeouts"]
    lines = [
        "# ASAP-BLOCK v9.3 Pilot-3 interim report", "",
        "This is a paused adaptive-screening checkpoint, not a completed formal experiment.", "",
        f"- Complete taskset-E0 instances: {summary['completed_taskset_e0_instances']}",
        f"- Complete analyses: {summary['completed_analyses']}",
        f"- Strict envelope accesses: {signals['strict_envelope_accesses']} / {signals['envelope_accesses']}",
        f"- Local-only closures: {signals['local_only_closures']}",
        f"- Strict response tasks: {signals['strict_response_tasks']}",
        f"- Certification gains: {signals['certification_gain_tasks']}",
        f"- 60-second timeouts / 90-second retries / retained timeouts: {timeout['initial_60_second_timeouts']} / {timeout['conditional_90_second_attempts']} / {timeout['timeout_at_90']}",
        f"- Preserved incomplete attempts: {len(summary['orphan_attempts_preserved'])}",
        "", "## Relation signals", "",
        "```json", json.dumps(signals["relations"], ensure_ascii=False, sort_keys=True, indent=2), "```", "",
        "No existing result was deleted or rerun when this checkpoint was written.",
    ]
    root.joinpath("pilot3_interim_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_screening(
    selected_e0: Sequence[Mapping[str, Any]],
    paired_tasksets: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    access_rows: Sequence[Mapping[str, Any]],
    closure_rows: Sequence[Mapping[str, Any]],
    response_rows: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate paired cardinalities and summarize the fourteen exact-E0 cells."""

    e0_values = [str(row["exact_e0"]) for row in selected_e0]
    utilizations = [format(float(value), ".1f") for value in config["screening"]["normalized_utilizations"]]
    expected_tasksets = len(utilizations) * int(config["screening"]["tasksets_per_utilization"])
    expected_instances = expected_tasksets * len(e0_values)
    expected_results = expected_instances * len(config["analysis"]["variants"])
    if len(paired_tasksets) != expected_tasksets:
        raise AnalysisError("paired taskset cardinality mismatch")
    if len(results) != expected_results:
        raise AnalysisError("screening result cardinality mismatch")
    keys = Counter((str(row["taskset_id"]), str(row["exact_e0"]), str(row["analysis_variant"])) for row in results)
    if len(keys) != expected_results or any(count != 1 for count in keys.values()):
        raise AnalysisError("missing or duplicate paired analysis")

    initial = [row for row in attempts if int(row["attempt_budget_seconds"]) == 60]
    retries = [row for row in attempts if int(row["attempt_budget_seconds"]) == 90]
    if len(initial) != expected_results:
        raise AnalysisError("every analysis must have exactly one 60-second attempt")
    initial_by_key = {
        (str(row["phase"]), str(row["taskset_id"]), str(row["exact_e0"]), str(row["analysis_variant"])): row
        for row in initial
    }
    if len(initial_by_key) != expected_results:
        raise AnalysisError("duplicate 60-second analysis attempt")
    retry_keys = Counter(
        (str(row["phase"]), str(row["taskset_id"]), str(row["exact_e0"]), str(row["analysis_variant"]))
        for row in retries
    )
    if any(count != 1 for count in retry_keys.values()):
        raise AnalysisError("duplicate 90-second analysis attempt")
    for retry in retries:
        key = (
            str(retry["phase"]), str(retry["taskset_id"]), str(retry["exact_e0"]),
            str(retry["analysis_variant"]),
        )
        if key not in initial_by_key or initial_by_key[key]["solver_status"] != "TIMEOUT":
            raise AnalysisError("90-second attempt was not gated by a 60-second timeout")

    input_hashes: Dict[str, set] = defaultdict(set)
    for row in results:
        input_hashes[str(row["taskset_id"])].add(str(row["task_input_hash"]))
    if any(len(values) != 1 for values in input_hashes.values()):
        raise AnalysisError("paired task input changed across exact E0 values")

    by_cell_results: Dict[tuple, list] = defaultdict(list)
    by_cell_access: Dict[tuple, list] = defaultdict(list)
    by_cell_closure: Dict[tuple, list] = defaultdict(list)
    by_cell_response: Dict[tuple, list] = defaultdict(list)
    for row in results:
        by_cell_results[(str(row["U_norm"]), str(row["exact_e0"]))].append(row)
    for collection, destination in (
        (access_rows, by_cell_access), (closure_rows, by_cell_closure),
        (response_rows, by_cell_response),
    ):
        for row in collection:
            destination[(str(row["U_norm"]), str(row["exact_e0"]))].append(row)

    relation_pairs = {
        "DEADLINE_CARRY_IN": ("CW_D", "LOC_D"),
        "FIXED_CW_CARRY_IN": ("CW_THETA_CW", "LOC_THETA_CW"),
        "RECURSIVE_CARRY_IN": ("CW_THETA_CW", "LOC_THETA_LOC"),
    }
    cells: Dict[str, Any] = {}
    for u_norm in utilizations:
        for e0 in e0_values:
            key = (u_norm, e0)
            cell_results = by_cell_results[key]
            access = by_cell_access[key]
            closures = by_cell_closure[key]
            responses = by_cell_response[key]
            result_lookup = {
                (str(row["taskset_id"]), str(row["analysis_variant"])): row
                for row in cell_results
            }
            certification_gain = set()
            for relation, (complete_name, local_name) in relation_pairs.items():
                for taskset_id in {str(row["taskset_id"]) for row in cell_results}:
                    complete = result_lookup[(taskset_id, complete_name)]
                    local = result_lookup[(taskset_id, local_name)]
                    if (
                        local["certification_status"] == "CERTIFIED_TASKSET"
                        and complete["certification_status"] != "CERTIFIED_TASKSET"
                    ):
                        certification_gain.add((taskset_id, relation))
            strict_responses = [row for row in responses if row["response_relation"] == "TIGHTER"]
            strict_tasksets = {str(row["taskset_id"]) for row in strict_responses}
            certification_gain_tasks = [row for row in responses if _truthy(row["certification_gain"])]
            metrics = {
                "tasksets": len({str(row["taskset_id"]) for row in cell_results}),
                "analyses": len(cell_results),
                "timeouts_after_retry": sum(row["final_solver_status"] == "TIMEOUT" for row in cell_results),
                "completed_or_decided": sum(row["final_solver_status"] != "TIMEOUT" for row in cell_results),
                "envelope_accesses": len(access),
                "envelope_strict_accesses": sum(row["envelope_relation"] == "STRICT" for row in access),
                "envelope_equal_accesses": sum(row["envelope_relation"] == "EQUAL" for row in access),
                "envelope_violations": sum(row["envelope_relation"] == "VIOLATION" for row in access),
                "predicted_interval_hits": sum(_truthy(row.get("predicted_interval_hit")) for row in access),
                "local_only_closures": len({
                    (str(row["taskset_id"]), str(row["relation"]), str(row["task_id"]), str(row["w"]), str(row["h"]))
                    for row in closures
                }),
                "response_common_candidate_tasks": sum(row["response_relation"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in responses),
                "response_strict_tasks": len(strict_responses),
                "response_strict_tasksets": len(strict_tasksets),
                "response_equal_tasks": sum(row["response_relation"] == "EQUAL" for row in responses),
                "response_violations": sum(row["response_relation"] == "VIOLATION" for row in responses),
                "certification_gain_tasks": len(certification_gain_tasks),
                "certification_gain_tasksets": len({item[0] for item in certification_gain}),
                "certification_gain_relations": len(certification_gain),
                "diagnostic_complete_tasks": sum(row["access_diagnostic_status"] == "TRACED_COMPLETE" for row in responses),
                "diagnostic_truncated_tasks": sum(row["access_diagnostic_status"] == "TRACED_TRUNCATED_60" for row in responses),
                "diagnostic_skipped_production_timeout_tasks": sum(row["access_diagnostic_status"] == "SKIPPED_PRODUCTION_TIMEOUT" for row in responses),
            }
            if any(row["access_diagnostic_status"] == "PENDING" for row in responses):
                raise AnalysisError("access diagnostic status was not finalized")
            cell_id = f"U{u_norm}-E0={e0}"
            cells[cell_id] = {
                "U_norm": u_norm, "exact_e0": e0, "class": classify_cell(metrics),
                **metrics, "timing": _timing(cell_results),
            }

    p0 = []
    if any(cell["envelope_violations"] for cell in cells.values()):
        p0.append("pointwise envelope dominance violation")
    if any(cell["response_violations"] for cell in cells.values()):
        p0.append("response dominance violation")
    if any(row["numeric_error"] == "True" or row["internal_error"] == "True" for row in results):
        p0.append("numeric or internal solver failure")
    ranked = sorted(
        (cell_id for cell_id, cell in cells.items() if cell["class"].startswith(("D_", "E_"))),
        key=lambda cell_id: (
            -cells[cell_id]["certification_gain_tasksets"],
            -cells[cell_id]["response_strict_tasksets"],
            -cells[cell_id]["response_strict_tasks"],
            cells[cell_id]["timeouts_after_retry"],
            -cells[cell_id]["response_common_candidate_tasks"],
            cell_id,
        ),
    )
    selected_confirmation = ranked[: int(config["confirmation"]["max_cells"])]
    relation_dominance = {}
    for relation in ("DEADLINE_CARRY_IN", "FIXED_CW_CARRY_IN", "RECURSIVE_CARRY_IN"):
        relation_rows = [row for row in response_rows if row["relation"] == relation]
        relation_dominance[relation] = {
            "common_candidate_tasks": sum(row["response_relation"] in {"TIGHTER", "EQUAL", "VIOLATION"} for row in relation_rows),
            "tighter": sum(row["response_relation"] == "TIGHTER" for row in relation_rows),
            "equal": sum(row["response_relation"] == "EQUAL" for row in relation_rows),
            "violations": sum(row["response_relation"] == "VIOLATION" for row in relation_rows),
        }
    return {
        "selected_exact_e0_count": len(e0_values),
        "paired_tasksets": expected_tasksets,
        "paired_instances": expected_instances,
        "analysis_results": expected_results,
        "initial_60_second_attempts": len(initial),
        "conditional_90_second_attempts": len(retries),
        "timeouts_after_retry": sum(row["final_solver_status"] == "TIMEOUT" for row in results),
        "cells": cells,
        "cell_class_distribution": dict(sorted(Counter(cell["class"] for cell in cells.values()).items())),
        "d_or_e_cells": ranked,
        "selected_confirmation_cells": selected_confirmation,
        "dominance": {
            "envelope_violations": sum(cell["envelope_violations"] for cell in cells.values()),
            "response_violations": sum(cell["response_violations"] for cell in cells.values()),
            "relations": relation_dominance,
        },
        "p0": p0,
    }


def summarize_confirmation(
    results: Sequence[Mapping[str, Any]], response_rows: Sequence[Mapping[str, Any]],
    selected_cells: Sequence[str],
) -> Dict[str, Any]:
    if not selected_cells:
        if results:
            raise AnalysisError("confirmation rows exist without selected cells")
        return {
            "executed": False, "selected_cells": [], "analysis_results": 0,
            "cells": {}, "parameter_identification_success": False,
        }
    by_cell_results: Dict[str, list] = defaultdict(list)
    by_cell_response: Dict[str, list] = defaultdict(list)
    for row in results:
        by_cell_results[str(row["cell_id"])].append(row)
    for row in response_rows:
        by_cell_response[str(row["cell_id"])].append(row)
    cells = {}
    for cell_id in selected_cells:
        rows = by_cell_results[cell_id]
        response = by_cell_response[cell_id]
        strict_tasksets = {
            str(row["taskset_id"]) for row in response if row["response_relation"] == "TIGHTER"
        }
        violations = sum(row["response_relation"] == "VIOLATION" for row in response)
        timeout_count = sum(row["final_solver_status"] == "TIMEOUT" for row in rows)
        cells[cell_id] = {
            "tasksets": len({str(row["taskset_id"]) for row in rows}),
            "analyses": len(rows),
            "timeouts_after_retry": timeout_count,
            "timeout_fraction": timeout_count / len(rows) if rows else None,
            "timeout_acceptability": "OBSERVED_NOT_THRESHOLDED",
            "certified_tasksets": sum(row["certification_status"] == "CERTIFIED_TASKSET" for row in rows),
            "response_strict_tasks": sum(row["response_relation"] == "TIGHTER" for row in response),
            "response_strict_tasksets": len(strict_tasksets),
            "response_violations": violations,
            "strict_response_reobserved": bool(strict_tasksets),
            "improvement_direction_consistent": violations == 0,
            "not_single_sample": len(strict_tasksets) >= 2,
        }
    identification_success = any(
        cell["strict_response_reobserved"] and cell["improvement_direction_consistent"]
        and cell["not_single_sample"] for cell in cells.values()
    )
    return {
        "executed": True, "selected_cells": list(selected_cells),
        "analysis_results": len(results), "cells": cells,
        "parameter_identification_success": identification_success,
    }


def write_report(root: Path, summary: Mapping[str, Any]) -> None:
    selection = summary["e0_selection"]
    screening = summary["screening"]
    confirmation = screening.get("confirmation", {"executed": False})
    lines = [
        "# ASAP-BLOCK v9.3 Pilot-3 exact-E0 report", "",
        "Pilot-3 is a paired diagnostic pilot, not a formal experiment or a paper-result claim.", "",
        "## Exact E0 selection", "",
        f"- Reconstructed strict separation intervals: {selection['interval_count']}",
        f"- Selected exact E0 values: {', '.join(selection['selected_values'])}",
        f"- Paired tasksets / instances / analyses: {screening['paired_tasksets']} / {screening['paired_instances']} / {screening['analysis_results']}",
        "", "## Screening", "",
        f"- Cell classes: `{json.dumps(screening['cell_class_distribution'], sort_keys=True)}`",
        f"- 60-second attempts: {screening['initial_60_second_attempts']}",
        f"- Conditional 90-second retries: {screening['conditional_90_second_attempts']}",
        f"- Timeouts after retry: {screening['timeouts_after_retry']}",
        f"- D/E cells: {', '.join(screening['d_or_e_cells']) or 'none'}",
        f"- Dominance violations: envelope={screening['dominance']['envelope_violations']}, response={screening['dominance']['response_violations']}",
        "- Response dominance by relation: `" + json.dumps(screening["dominance"]["relations"], sort_keys=True) + "`",
        "", "## Confirmation", "",
        f"- Executed: {confirmation.get('executed', False)}",
        f"- Selected cells: {', '.join(confirmation.get('selected_cells', [])) or 'none'}",
        "", "## Decision", "",
    ]
    if screening["p0"]:
        lines.append("Pilot-3 is not ready: " + "; ".join(screening["p0"]) + ".")
    elif screening["d_or_e_cells"]:
        lines.append("At least one legal exact-E0 region changed a response candidate or certification outcome; use only the predeclared confirmation results when judging reproducibility. Parameter identification success after confirmation: " + str(confirmation.get("parameter_identification_success", False)) + ".")
    else:
        earliest = summary.get("earliest_structural_boundary", {})
        lines.extend([
            "No D/E cell was observed. The scan stops without adding E0 points.",
            f"Predicted separation was hit at {earliest.get('predicted_coverage_hit_count', 0)} observed access points; local-only closures: {sum(cell['local_only_closures'] for cell in screening['cells'].values())}.",
            "The earliest strict access/local-only closure did not move an earliest response candidate; closures before complete/local candidates were "
            f"{earliest.get('local_only_closures_before_complete_candidate', 0)}/"
            f"{earliest.get('local_only_closures_before_local_candidate', 0)}.",
            "The next pilot should change exactly one structural dimension: " + str(earliest.get("future_dimension", "deadline structure")) + ".",
        ])
    root.joinpath("pilot3_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
