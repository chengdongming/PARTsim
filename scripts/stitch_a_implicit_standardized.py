#!/usr/bin/env python3
"""Fail-closed stitcher for the standardized A-implicit paper domain.

This tool reads three completed source roots and writes explicitly composite
outputs. It never edits a source root and never rewrites a source identity.
"""

from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import perf_g  # noqa: E402
from experiments.v9_3 import scheduler_load_cross as experiment  # noqa: E402


STANDARDIZED_DOMAIN = "0.1_to_0.9"
COMPOSITE_CONTRACT = "implicit-d-equals-t-standardized-composite-v1"
FORMAL_SAMPLES = 120
STANDARDIZED_SCAN = tuple(Fraction(value) for value in (
    "1/10", "1/5", "3/10", "2/5", "1/2", "3/5", "7/10", "4/5", "9/10",
))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read JSONL: {path}") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{number}") from exc
        if not isinstance(value, dict):
            raise SystemExit(f"JSON object required at {path}:{number}")
        rows.append(value)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _fraction(value: Any, label: str) -> Fraction:
    try:
        return Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"invalid fraction for {label}: {value!r}") from exc


def _expected_source(label: str) -> tuple[str, str, dict[str, Any]]:
    if label == "legacy_uc":
        return (
            experiment.A_IMPLICIT_EXPERIMENT,
            experiment.A_IMPLICIT_DOMAIN,
            experiment.a_implicit_campaign_spec(
                experiment.A_IMPLICIT_UC_FIXED_SUPPLY_CAMPAIGN
            ),
        )
    if label == "legacy_ue":
        return (
            experiment.A_IMPLICIT_EXPERIMENT,
            experiment.A_IMPLICIT_DOMAIN,
            experiment.a_implicit_campaign_spec(
                experiment.A_IMPLICIT_UE_SERVICE_SCALING_CAMPAIGN
            ),
        )
    if label == "uc09_supplement":
        return (
            experiment.A_IMPLICIT_V2_EXPERIMENT,
            experiment.A_IMPLICIT_V2_DOMAIN,
            experiment.a_implicit_uc09_supplement_spec(),
        )
    raise SystemExit(f"unknown source label: {label}")


def _common_config_checks(config: dict[str, Any], label: str) -> None:
    exact = {
        "seed": 20260906,
        "processors": 4,
        "tasks": 10,
        "period_min": 40,
        "period_max": 200,
        "kappa": "10",
        "initial_energy_rule": "battery_capacity/2",
        "simulation_horizon_ms": 60000,
        "priority_policy": "RM",
        "deadline_modes": ["implicit"],
        "deadline_semantics": "D=T; RM=DM; canonical source=RM",
        "wholepass_fast_path": True,
        "full_trace_default": False,
        "dmr_available": False,
    }
    for key, value in exact.items():
        if config.get(key) != value:
            raise SystemExit(f"{label}: config {key} does not match the frozen contract")
    for key, value in experiment.HARVEST_MODEL_IDENTITY.items():
        if config.get(key) != value:
            raise SystemExit(f"{label}: harvest model field {key} mismatch")
    if list(config.get("schedulers", ())) != list(perf_g.FORMAL_SCHEDULERS):
        raise SystemExit(f"{label}: all nine canonical schedulers are required")
    if config.get("min_task_util") != str(perf_g.MIN_TASK_UTILIZATION):
        raise SystemExit(f"{label}: min task utilization mismatch")
    if config.get("max_task_util") != str(perf_g.MAX_TASK_UTILIZATION):
        raise SystemExit(f"{label}: max task utilization mismatch")
    if config.get("util_tolerance_total") != str(perf_g.UTILIZATION_TOLERANCE):
        raise SystemExit(f"{label}: utilization tolerance mismatch")
    if config.get("canonical_taskset_source") != "PERF-G TasksetStore":
        raise SystemExit(f"{label}: canonical taskset source mismatch")
    if config.get("energy_unit") != "J/tick exact canonical P":
        raise SystemExit(f"{label}: task power semantics mismatch")


def _check_no_trace(root: Path, label: str) -> None:
    if any(path.name == "simulation_trace_work" for path in root.rglob("simulation_trace_work")):
        raise SystemExit(f"{label}: semantic trace directory is forbidden")


def _validate_source(root: Path, label: str) -> dict[str, Any]:
    config = _read_json(root / "run_config.json")
    expected_experiment, expected_domain, spec = _expected_source(label)
    if config.get("experiment") != expected_experiment or config.get("domain") != expected_domain:
        raise SystemExit(f"{label}: experiment/domain mismatch")
    if config.get("campaign") != spec["campaign"]:
        raise SystemExit(f"{label}: campaign mismatch")
    if config.get("campaign_contract") != spec["campaign_contract"]:
        raise SystemExit(f"{label}: campaign contract mismatch")
    if config.get("energy_control") != spec["energy_control"]:
        raise SystemExit(f"{label}: energy control mismatch")
    _common_config_checks(config, label)
    if config.get("scan_contract") != spec["scan_contract"] or config.get("figure_slices") != spec["figure_slices"]:
        raise SystemExit(f"{label}: scan or figure contract mismatch")
    configured_cells = tuple(
        (_fraction(row[0], "configured U_C"), _fraction(row[1], "configured U_E"))
        for row in config.get("cells", ())
    )
    if configured_cells != tuple(spec["cells"]):
        raise SystemExit(f"{label}: configured cells mismatch")
    if config.get("run_identity") != experiment.run_identity(config):
        raise SystemExit(f"{label}: run_identity is invalid")
    samples = int(config.get("samples_per_cell", 0))
    if samples < 1:
        raise SystemExit(f"{label}: invalid samples_per_cell")
    if label == "uc09_supplement":
        if config.get("supplement_kind") != "uc09":
            raise SystemExit("uc09_supplement: supplement_kind is missing")
        if config.get("generation_grid_utilizations") != [str(value) for value in STANDARDIZED_SCAN]:
            raise SystemExit("uc09_supplement: canonical generation grid provenance mismatch")
        if config.get("queued_grid_utilizations") != ["9/10"]:
            raise SystemExit("uc09_supplement: queued grid provenance mismatch")
    invariant_path = root / "invariant_report.json"
    if not invariant_path.is_file():
        raise SystemExit(f"{label}: invariant_report.json is required")
    invariant = _read_json(invariant_path)
    for key in ("technical", "missing", "missing_results", "duplicate", "duplicate_request_ids", "unexpected"):
        if invariant.get(key, 0) != 0:
            raise SystemExit(f"{label}: invariant report has nonzero {key}")
    if label == "legacy_ue" and invariant.get("runtime_config_ue_exact") is not True:
        raise SystemExit("legacy_ue: runtime_config_ue_exact must be true")
    tasksets = _read_jsonl(root / "tasksets.jsonl")
    requests = _read_jsonl(root / "requests.jsonl")
    results = _read_jsonl(root / "results.jsonl")
    expected_results = len(spec["cells"]) * samples * len(perf_g.FORMAL_SCHEDULERS)
    if len(requests) != expected_results or len(results) != expected_results:
        raise SystemExit(f"{label}: result count does not match source contract")
    if config.get("expected_request_count") != expected_results:
        raise SystemExit(f"{label}: expected_request_count is inconsistent")
    if len(tasksets) != len({uc for uc, _ue in spec["cells"]}) * samples:
        raise SystemExit(f"{label}: taskset count does not match source contract")
    taskset_by_id = {str(row.get("taskset_id")): row for row in tasksets}
    if len(taskset_by_id) != len(tasksets):
        raise SystemExit(f"{label}: duplicate taskset identity")
    for taskset in tasksets:
        if taskset.get("deadline_mode") != "implicit" or taskset.get("M") != 4 or taskset.get("task_n") != 10:
            raise SystemExit(f"{label}: taskset dimensions/deadline mismatch")
        try:
            payload = json.loads(taskset["task_input_json"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"{label}: invalid task payload") from exc
        if len(payload) != 10 or any(
            not (0 < int(item["C"]) <= int(item["D"]) <= int(item["T"]))
            or int(item["D"]) != int(item["T"])
            for item in payload
        ):
            raise SystemExit(f"{label}: taskset violates D=T")
    request_ids = [str(row.get("request_id")) for row in requests]
    result_ids = [str(row.get("request_id")) for row in results]
    if len(request_ids) != len(set(request_ids)) or len(result_ids) != len(set(result_ids)) or set(request_ids) != set(result_ids):
        raise SystemExit(f"{label}: missing, duplicate, or unexpected request IDs")
    expected_uc = {str(uc) for uc, _ue in spec["cells"]}
    expected_ue = {str(ue) for _uc, ue in spec["cells"]}
    for row in requests + results:
        if (
            row.get("experiment") != expected_experiment
            or row.get("domain") != expected_domain
            or row.get("campaign") != spec["campaign"]
            or row.get("energy_control") != spec["energy_control"]
            or row.get("priority_policy") != "RM"
            or row.get("deadline_mode") != "implicit"
            or row.get("fast_mode") not in (None, "a_implicit_rm_hardrt_wholepass")
        ):
            raise SystemExit(f"{label}: row identity or fast-path mismatch")
        if str(row.get("target_uc")) not in expected_uc or str(row.get("target_ue")) not in expected_ue:
            raise SystemExit(f"{label}: row outside source cell grid")
    for row in results:
        if row.get("technical_error") is not None or row.get("simulation_status") not in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}:
            raise SystemExit(f"{label}: technical result is not scientific")
        if row.get("fast_mode") != "a_implicit_rm_hardrt_wholepass":
            raise SystemExit(f"{label}: result is not compact WholePass")
        outcome = row.get("outcome")
        if not isinstance(outcome, dict) or set(outcome) - {"outcome_status", "wholepass", "taskset_pass"}:
            raise SystemExit(f"{label}: DMR-like outcome is forbidden")
        taskset = taskset_by_id.get(str(row.get("taskset_id")))
        if taskset is None or row.get("taskset_hash") != taskset.get("taskset_hash"):
            raise SystemExit(f"{label}: taskset identity mismatch")
        if spec["energy_control"] == "FIXED_ABSOLUTE_SUPPLY":
            level = str(row.get("energy_level"))
            energy = row.get("energy")
            expected_level = config["fixed_supply_levels"].get(level)
            if not isinstance(energy, dict) or not isinstance(expected_level, dict):
                raise SystemExit(f"{label}: fixed-supply energy material is incomplete")
            if energy.get("fixed_supply_mean_j_per_tick") != expected_level["fixed_supply_mean_j_per_tick"]:
                raise SystemExit(f"{label}: fixed-supply value is not the exact canonical value")
    expected_groups = {
        (str(uc), str(ue), index)
        for uc, ue in spec["cells"] for index in range(samples)
    }
    observed_groups = {
        (str(row.get("target_uc")), str(row.get("target_ue")), int(row.get("generation_index")))
        for row in requests
    }
    if observed_groups != expected_groups:
        raise SystemExit(f"{label}: missing cell/taskset coverage")
    if spec["energy_control"] == "FIXED_ABSOLUTE_SUPPLY":
        exact_levels = {str(value): level for level, value in experiment.V7_REFERENCE_UES.items()}
        for row in results:
            if row.get("energy_level") != exact_levels.get(str(row.get("target_ue"))):
                raise SystemExit(f"{label}: fixed-supply level mismatch")
    if label == "legacy_uc" and {str(value) for value in experiment.A_IMPLICIT_UC_SCAN} != expected_uc:
        raise SystemExit("legacy_uc: U_C grid is not exactly 0.1..0.8")
    if label == "legacy_ue" and {str(value) for value in experiment.A_IMPLICIT_UE_SCAN} != expected_ue:
        raise SystemExit("legacy_ue: U_E grid is not exactly 0.1..1.0")
    if label == "uc09_supplement" and expected_uc != {"9/10"}:
        raise SystemExit("uc09_supplement: U_C must be exactly 0.9")
    _check_no_trace(root, label)
    return {
        "label": label, "root": str(root.resolve()), "config": config,
        "spec": spec, "samples": samples, "tasksets": tasksets,
        "requests": requests, "results": results,
    }


def _check_scientific_compatibility(sources: list[dict[str, Any]]) -> None:
    keys = (
        "seed", "processors", "tasks", "period_min", "period_max", "min_task_util",
        "max_task_util", "util_tolerance_total", "kappa", "initial_energy_rule",
        "simulation_horizon_ms", "priority_policy", "deadline_modes", "deadline_semantics",
        "wholepass_fast_path", "full_trace_default", "dmr_available", "schedulers",
        "harvest_model", "period_ms", "minimum_factor", "maximum_factor", "mean_factor",
        "breakpoints_ms", "integration_contract", "periodic", "reference_service_curve",
    )
    baseline = sources[0]["config"]
    for source in sources[1:]:
        if any(source["config"].get(key) != baseline.get(key) for key in keys):
            raise SystemExit(f"scientific configuration mismatch: {source['label']}")


def _composite_rows(source: dict[str, Any], rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        output.append({
            **row,
            "source_dataset": source["label"],
            "source_root": source["root"],
            "source_request_id": str(row["request_id"]),
            "source_run_identity": source["config"]["run_identity"],
            "composite_contract": COMPOSITE_CONTRACT,
            "standardized_domain": STANDARDIZED_DOMAIN,
        })
    return output


def _check_scientific_keys(rows: list[dict[str, Any]], label: str) -> None:
    keys = set()
    for row in rows:
        key = (str(row["target_uc"]), str(row["target_ue"]), int(row["generation_index"]), str(row["scheduler"]), str(row.get("energy_level", "service_scaling")))
        if key in keys:
            raise SystemExit(f"{label}: duplicate scientific scheduler/taskset/cell key")
        keys.add(key)


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        level = str(row.get("energy_level", "service_scaling"))
        grouped.setdefault((str(row["target_uc"]), str(row["target_ue"]), str(row["scheduler"]), level), []).append(row)
    result = []
    for (uc, ue, scheduler, level), selected in sorted(grouped.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]), item[0][2], item[0][3])):
        passed = sum(row.get("wholepass") is True for row in selected)
        result.append({
            "target_uc": uc, "target_ue": ue, "scheduler": scheduler,
            "energy_level": level, "n_total": len(selected),
            "n_wholepass": passed, "wholepass_ratio": passed / len(selected),
            "source_dataset": selected[0]["source_dataset"],
            "composite_contract": COMPOSITE_CONTRACT,
        })
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["target_uc", "target_ue", "scheduler"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _plot(path: Path, rows: list[dict[str, Any]], *, axis: str, fixed: list[tuple[str, str]], xlabel: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("matplotlib is required for standardized figures") from exc
    styles = {name: style for name, style in zip(perf_g.FORMAL_SCHEDULERS, ("-", "--", ":", "-.", (0, (3, 1, 1, 1)), (0, (5, 1)), (0, (1, 1)), (0, (5, 2, 1, 2)), (0, (2, 2))))}
    fig, axes = plt.subplots(len(fixed), 1, figsize=(10, 3.0 * len(fixed)), squeeze=False, sharex=True)
    for index, (fixed_value, label) in enumerate(fixed):
        ax = axes[index][0]
        for scheduler in perf_g.FORMAL_SCHEDULERS:
            selected = [row for row in rows if row["scheduler"] == scheduler and str(row["target_ue"] if axis == "target_uc" else row["target_uc"]) == fixed_value]
            selected.sort(key=lambda row: Fraction(row[axis]))
            ax.plot([float(Fraction(row[axis])) for row in selected], [row["wholepass_ratio"] for row in selected], marker="o", linestyle=styles[scheduler], label=scheduler)
        ax.set_xlim(0.1, 0.9)
        ax.set_xticks([float(value) for value in STANDARDIZED_SCAN])
        ax.set_ylim(0, 1)
        ax.grid(True, alpha=0.25)
        ax.set_ylabel("WholePass")
        ax.set_title(label)
    axes[-1][0].set_xlabel(xlabel)
    axes[0][0].legend(ncol=3, fontsize=8)
    fig.suptitle("Implicit deadlines (D=T; RM=DM; canonical RM run) — Whole-taskset pass ratio", y=1.01)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def stitch(legacy_uc_root: Path, legacy_ue_root: Path, supplement_root: Path, output: Path, *, test_only: bool = False) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output exists and is non-empty: {output}")
    uc = _validate_source(legacy_uc_root, "legacy_uc")
    ue = _validate_source(legacy_ue_root, "legacy_ue")
    supplement = _validate_source(supplement_root, "uc09_supplement")
    _check_scientific_compatibility([uc, ue, supplement])
    if test_only and supplement["samples"] != FORMAL_SAMPLES:
        _write_json(output / "stitch_validation.json", {
            "complete": False, "test_only": True,
            "reason": "supplement sample count differs from formal legacy roots; formal merge rejected",
            "legacy_samples": {"uc": uc["samples"], "ue": ue["samples"]},
            "supplement_samples": supplement["samples"],
        })
        return {"complete": False, "test_only": True, "supplement_samples": supplement["samples"]}
    if uc["samples"] != FORMAL_SAMPLES or ue["samples"] != FORMAL_SAMPLES or supplement["samples"] != FORMAL_SAMPLES:
        raise SystemExit("formal stitch requires N=120 in all three source roots")
    uc_rows = _composite_rows(uc, uc["results"]) + _composite_rows(supplement, supplement["results"])
    ue_rows = _composite_rows(ue, [row for row in ue["results"] if _fraction(row["target_ue"], "target U_E") <= Fraction(9, 10)])
    if len(uc_rows) != 29160 or len(ue_rows) != 29160:
        raise SystemExit("standardized result counts must be 29160 per axis")
    _check_scientific_keys(uc_rows, "standardized UC")
    _check_scientific_keys(ue_rows, "standardized UE")
    if {str(row["target_uc"]) for row in uc_rows} != {str(value) for value in STANDARDIZED_SCAN}:
        raise SystemExit("standardized UC points are not exactly 0.1..0.9")
    if {str(row["target_ue"]) for row in ue_rows} != {str(value) for value in STANDARDIZED_SCAN}:
        raise SystemExit("standardized UE points are not exactly 0.1..0.9")
    excluded_ue = [row for row in ue["results"] if _fraction(row["target_ue"], "target U_E") == 1]
    if len(excluded_ue) != 3240:
        raise SystemExit("exactly 3240 legacy U_E=1.0 rows must be preserved but excluded")
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "standardized_uc_results.jsonl", uc_rows)
    _write_jsonl(output / "standardized_ue_results.jsonl", ue_rows)
    _write_jsonl(output / "composite_results.jsonl", uc_rows + ue_rows)
    uc_summary = _summary(uc_rows)
    ue_summary = _summary(ue_rows)
    _write_csv(output / "standardized_uc_summary.csv", uc_summary)
    _write_csv(output / "standardized_ue_summary.csv", ue_summary)
    _plot(output / "figure_scheduler_uc_slices.png", uc_summary, axis="target_uc", fixed=[("9/10", "low"), ("3/4", "medium"), ("3/5", "high")], xlabel="U_C")
    _plot(output / "figure_scheduler_ue_slices.png", ue_summary, axis="target_ue", fixed=[("3/10", "U_C=0.3"), ("1/2", "U_C=0.5"), ("7/10", "U_C=0.7")], xlabel="U_E")
    manifest = {
        "complete": True, "composite_contract": COMPOSITE_CONTRACT,
        "standardized_domain": STANDARDIZED_DOMAIN,
        "legacy_uc_results_reused": len(uc["results"]),
        "supplement_uc09_results_added": len(supplement["results"]),
        "old_ue_0.1_to_0.9_results_reused": len(ue_rows),
        "old_ue_1.0_results_preserved_but_excluded": len(excluded_ue),
        "final_standardized_uc_rows": len(uc_rows),
        "final_standardized_ue_rows": len(ue_rows),
        "final_standardized_total_rows": len(uc_rows) + len(ue_rows),
        "standardized_uc_domain": [str(value) for value in STANDARDIZED_SCAN],
        "standardized_ue_domain": [str(value) for value in STANDARDIZED_SCAN],
        "axis_display_min": "1/10", "axis_display_max": "9/10",
        "axis_tick_step": "1/10",
        "source_roots": {
            "legacy_uc": uc["root"], "legacy_ue": ue["root"],
            "uc09_supplement": supplement["root"],
        },
        "source_run_identities": {source["label"]: source["config"]["run_identity"] for source in (uc, ue, supplement)},
        "dmr_available": False, "wholepass_only": True,
    }
    _write_json(output / "composite_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-uc-root", type=Path, required=True)
    parser.add_argument("--legacy-ue-root", type=Path, required=True)
    parser.add_argument("--uc09-supplement-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-only", action="store_true")
    args = parser.parse_args(argv)
    result = stitch(args.legacy_uc_root, args.legacy_ue_root, args.uc09_supplement_root, args.output, test_only=args.test_only)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
