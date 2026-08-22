#!/usr/bin/env python3
"""Analyze paired scheduler LOAD-CROSS results."""

from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.v9_3 import scheduler_load_cross as experiment


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0]) if rows else ["target_uc", "target_ue", "scheduler", "acceptance_ratio"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


def _configured_cells(config: dict[str, Any]) -> tuple[tuple[Fraction, Fraction], ...]:
    raw_cells = config.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise SystemExit("run_config cells are missing or invalid")
    cells = []
    for index, raw_cell in enumerate(raw_cells):
        if not isinstance(raw_cell, list) or len(raw_cell) != 2:
            raise SystemExit(f"run_config cell {index} is invalid")
        try:
            cell = (
                experiment.parse_fraction(raw_cell[0], f"cells[{index}].U_C"),
                experiment.parse_fraction(raw_cell[1], f"cells[{index}].U_E"),
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not all(0 < value <= 1 for value in cell):
            raise SystemExit(f"run_config cell {index} is outside (0,1]")
        if cell not in cells:
            cells.append(cell)
    if len(cells) != len(raw_cells):
        raise SystemExit("run_config cells contain duplicates")
    return tuple(cells)


def _figure_slices(config: dict[str, Any], cells: tuple[tuple[Fraction, Fraction], ...]) -> dict[str, dict[str, str]]:
    raw_slices = config.get("figure_slices")
    if raw_slices is None:
        try:
            return experiment.resolve_figure_slices(cells)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if not isinstance(raw_slices, dict):
        raise SystemExit("run_config figure_slices is invalid")
    expected = {
        "uc_scan": ("target_uc", "target_ue"),
        "ue_scan": ("target_ue", "target_uc"),
    }
    normalized: dict[str, dict[str, str]] = {}
    for name, (x_key, fixed_key) in expected.items():
        entry = raw_slices.get(name)
        if not isinstance(entry, dict):
            raise SystemExit(f"run_config figure_slices.{name} is missing")
        if entry.get("x_key") != x_key or entry.get("fixed_key") != fixed_key:
            raise SystemExit(f"run_config figure_slices.{name} has invalid axes")
        try:
            fixed_value = experiment.parse_fraction(
                entry.get("fixed_value"), f"figure_slices.{name}.fixed_value",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        canonical = experiment.fraction_text(fixed_value)
        if entry.get("fixed_value") != canonical:
            raise SystemExit(f"run_config figure_slices.{name}.fixed_value is not canonical")
        normalized[name] = {
            "x_key": x_key, "fixed_key": fixed_key, "fixed_value": canonical,
        }
    try:
        experiment.resolve_figure_slices(
            cells,
            fixed_ue=experiment.parse_fraction(normalized["uc_scan"]["fixed_value"], "fixed U_E"),
            fixed_uc=experiment.parse_fraction(normalized["ue_scan"]["fixed_value"], "fixed U_C"),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return normalized


def select_scan_rows(
    summaries: list[dict[str, Any]], fixed_key: str, fixed_value: str,
) -> list[dict[str, Any]]:
    target = Fraction(fixed_value)
    return [row for row in summaries if Fraction(row[fixed_key]) == target]


def _validate_scan_rows(
    rows: list[dict[str, Any]], cells: tuple[tuple[Fraction, Fraction], ...],
    *, fixed_key: str, x_key: str, fixed_value: str, label: str,
) -> None:
    fixed_index = 1 if fixed_key == "target_ue" else 0
    x_index = 0 if x_key == "target_uc" else 1
    expected_x = {
        experiment.fraction_text(cell[x_index]) for cell in cells
        if experiment.fraction_text(cell[fixed_index]) == fixed_value
    }
    observed_x = {experiment.fraction_text(Fraction(row[x_key])) for row in rows}
    if not expected_x or observed_x != expected_x:
        raise SystemExit(f"{label} slice does not match configured cells")
    if not any(row["acceptance_ratio"] is not None for row in rows):
        raise SystemExit(f"{label} slice has no valid result rows")


def plot_scan(
    rows: list[dict[str, Any]], output: Path, filename: str, xkey: str,
    schedulers: list[str], xlabel: str, title: str,
) -> None:
    import matplotlib.pyplot as plt

    plt.figure()
    for scheduler in schedulers:
        values = [
            row for row in rows
            if row["scheduler"] == scheduler and row["acceptance_ratio"] is not None
        ]
        values.sort(key=lambda row: Fraction(row[xkey]))
        plt.plot(
            [float(Fraction(row[xkey])) for row in values],
            [row["acceptance_ratio"] for row in values],
            marker="o", label=scheduler,
        )
    plt.xlabel(xlabel)
    plt.ylabel("Schedulability ratio")
    plt.title(title)
    plt.ylim(0, 1.05)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output / filename)
    plt.close()


def analyze(root: Path) -> dict[str, Any]:
    config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
    cells = _configured_cells(config)
    figure_slices = _figure_slices(config, cells)
    tasksets = read_jsonl(root / "tasksets.jsonl")
    requests = read_jsonl(root / "requests.jsonl")
    results = read_jsonl(root / "results.jsonl")
    expected = {str(row["request_id"]) for row in requests}
    observed = [str(row.get("request_id")) for row in results]
    duplicate = len(observed) - len(set(observed))
    missing = len(expected - set(observed))
    unexpected = len(set(observed) - expected)
    if duplicate or missing or unexpected:
        raise SystemExit(f"incomplete campaign: duplicate={duplicate} missing={missing} unexpected={unexpected}")
    expected_tasksets = len({row["taskset_id"] for row in requests})
    taskset_by_id = {str(row["taskset_id"]): row for row in tasksets}
    if len(taskset_by_id) != expected_tasksets:
        raise SystemExit("taskset identity count mismatch")
    if any(row.get("canonical_task_power") is not True for row in tasksets):
        raise SystemExit("non-canonical task power in taskset store")
    uc_tolerance = Fraction(str(config["util_tolerance_total"])) / Fraction(str(config["processors"]))
    if any(
        abs(Fraction(row["actual_uc"]) - Fraction(row["target_uc"])) > uc_tolerance
        for row in tasksets
    ):
        raise SystemExit("actual U_C exceeds configured tolerance")
    for row in results:
        if (
            row.get("technical_error") is not None
            or row.get("simulation_status") not in {
                "SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS",
            }
        ):
            raise SystemExit("active results contain a technical or non-terminal row")
        taskset = taskset_by_id[str(row["taskset_id"])]
        if row["taskset_hash"] != taskset["taskset_hash"]:
            raise SystemExit("scheduler changed taskset identity")
        energy = row["energy"]
        ue = Fraction(row["target_ue"])
        if Fraction(energy["eta"]) != experiment.eta_for_ue(ue):
            raise SystemExit("eta != 1/U_E")
        demand = Fraction(energy["P_dem_j_per_tick"])
        supply = Fraction(energy["target_supply_mean_j_per_tick"])
        raw = Fraction(energy["raw_reference_mean_j_per_tick"])
        if demand / supply != ue or Fraction(energy["solar_scale"]) * raw != supply:
            raise SystemExit("U_E service identity mismatch")
    schedulers = list(config["schedulers"])
    paired_tasksets: dict[tuple[str, str], set[str]] = {}
    for request in requests:
        paired_tasksets.setdefault(
            (str(request["target_uc"]), str(request["generation_index"])),
            set(),
        ).add(str(request["taskset_id"]))
    if any(len(values) != 1 for values in paired_tasksets.values()):
        raise SystemExit("paired CPU taskset identity changed across U_E")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in results:
        groups.setdefault((str(row["target_uc"]), str(row["target_ue"])), []).append(row)
    summaries = []
    for (uc, ue), group in sorted(groups.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]))):
        for scheduler in schedulers:
            selected = [row for row in group if row["scheduler"] == scheduler]
            if len(selected) != int(config["samples_per_cell"]):
                raise SystemExit("missing scheduler result in cell")
            n_total = len(selected)
            n_schedulable = sum(row.get("schedulable") is True for row in selected)
            n_miss = sum(row.get("deadline_miss") is True for row in selected)
            n_timeout = sum(row.get("simulation_status") == "SIM_RUNTIME_TIMEOUT" for row in selected)
            n_internal = sum(row.get("simulation_status") == "SIM_INTERNAL_ERROR" for row in selected)
            n_other = sum(row.get("technical_error") is not None for row in selected) - n_timeout - n_internal
            technical = n_timeout + n_internal + n_other
            summaries.append({
                "target_uc": uc, "target_ue": ue, "scheduler": scheduler,
                "n_total": n_total, "n_schedulable": n_schedulable,
                "n_deadline_miss": n_miss, "n_timeout": n_timeout,
                "n_internal_error": n_internal, "n_other_technical_error": n_other,
                "acceptance_ratio": None if technical else n_schedulable / n_total,
            })
    uc_slice = figure_slices["uc_scan"]
    ue_slice = figure_slices["ue_scan"]
    uc_rows = select_scan_rows(
        summaries, uc_slice["fixed_key"], uc_slice["fixed_value"],
    )
    ue_rows = select_scan_rows(
        summaries, ue_slice["fixed_key"], ue_slice["fixed_value"],
    )
    _validate_scan_rows(
        uc_rows, cells, fixed_key=uc_slice["fixed_key"], x_key=uc_slice["x_key"],
        fixed_value=uc_slice["fixed_value"], label="U_C",
    )
    _validate_scan_rows(
        ue_rows, cells, fixed_key=ue_slice["fixed_key"], x_key=ue_slice["x_key"],
        fixed_value=ue_slice["fixed_value"], label="U_E",
    )
    write_csv(root / "summary.csv", summaries)
    write_csv(root / "figure_scheduler_uc.csv", uc_rows)
    write_csv(root / "figure_scheduler_ue.csv", ue_rows)
    try:
        import matplotlib
        matplotlib.use("Agg")
        plot_scan(
            uc_rows, root, "figure_scheduler_uc.png", uc_slice["x_key"], schedulers,
            "U_C", f"Schedulability ratio versus U_C (U_E={uc_slice['fixed_value']})",
        )
        plot_scan(
            ue_rows, root, "figure_scheduler_ue.png", ue_slice["x_key"], schedulers,
            "U_E", f"Schedulability ratio versus U_E (U_C={ue_slice['fixed_value']})",
        )
    except ImportError:
        pass
    report = {"complete": True, "tasksets": len(tasksets), "requests": len(requests),
              "results": len(results), "duplicate_request_ids": duplicate,
              "missing_request_ids": missing, "summary_rows": len(summaries),
              "technical_result_count": sum(bool(row.get("technical_error")) for row in results)}
    (root / "analysis_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.input), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
