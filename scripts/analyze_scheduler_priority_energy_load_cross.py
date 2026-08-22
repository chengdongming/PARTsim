#!/usr/bin/env python3
"""Analyze priority-energy correlated Scheduler LOAD-CROSS results."""

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

from experiments.v9_3 import scheduler_priority_energy_load_cross as experiment
from experiments.v9_3.parallel_prepare import validate_workers


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _plot(rows: list[dict[str, Any]], path: Path, x_key: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for scheduler in sorted({row["scheduler"] for row in rows}):
        selected = [row for row in rows if row["scheduler"] == scheduler]
        selected.sort(key=lambda row: Fraction(row[x_key]))
        plt.plot([float(Fraction(row[x_key])) for row in selected],
                 [row["acceptance_ratio"] for row in selected], marker="o", label=scheduler)
    plt.xlabel(x_key)
    plt.ylabel("acceptance ratio")
    plt.title(title)
    plt.ylim(0, 1)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def analyze(root: Path, *, analysis_workers: int = 1) -> dict[str, Any]:
    validate_workers(analysis_workers, "analysis-workers")
    config = json.loads((root / "run_config.json").read_text(encoding="utf-8"))
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
    if any(row.get("all_workloads_hash") is not True for row in tasksets):
        raise SystemExit("base taskset contains a non-hash workload")
    ratios = tuple(Fraction(value) for value in config["priority_energy_ratios"])
    if Fraction(config["priority_energy_reference_ratio"]) != experiment.REFERENCE_RATIO:
        raise SystemExit("reference ratio is not 2")
    taskset_ids = {str(row["taskset_id"]) for row in tasksets}
    if len(taskset_ids) != len(tasksets):
        raise SystemExit("duplicate base taskset IDs")

    material_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    battery_by_pair: dict[tuple[str, str, str], str] = {}
    for row in results:
        if row.get("technical_error") is not None or row.get("simulation_status") not in {
            "SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS",
        }:
            raise SystemExit("active results contain a technical result")
        material = row["material"]
        rho = Fraction(material["rho"])
        if rho not in ratios or Fraction(material["rho_reference"]) != experiment.REFERENCE_RATIO:
            raise SystemExit("invalid rho provenance")
        material_without_hash = {key: value for key, value in material.items() if key != "material_hash"}
        if (
            material["material_hash"] != row["material_hash"]
            or experiment._hash("MATERIAL", material_without_hash) != material["material_hash"]
        ):
            raise SystemExit("material hash mismatch")
        base = Fraction(material["P_dem_base"])
        transformed = Fraction(material["P_dem_transformed"])
        if base != transformed:
            raise SystemExit("P_dem conservation failed")
        high = Fraction(material["H_base"])
        low = Fraction(material["L_base"])
        expected_high, expected_low = experiment.factors_for_ratio(high, low, rho)
        if Fraction(material["high_factor"]) != expected_high or Fraction(material["low_factor"]) != expected_low:
            raise SystemExit("factor formula mismatch")
        for task in material["tasks"]:
            if task["workload"] != "hash":
                raise SystemExit("transformed task workload is not hash")
            factor = Fraction(task["exact_factor"])
            if Fraction(task["transformed_P"]) != Fraction(task["base_P"]) * factor:
                raise SystemExit("task transformed P mismatch")
        energy = row["energy"]
        ue = Fraction(row["target_ue"])
        if Fraction(energy["eta"]) != Fraction(1, 1) / ue:
            raise SystemExit("eta identity failed")
        if Fraction(energy["P_dem_j_per_tick"]) != transformed:
            raise SystemExit("energy demand is not transformed demand")
        if Fraction(energy["P_dem_j_per_tick"]) / Fraction(energy["target_supply_mean_j_per_tick"]) != ue:
            raise SystemExit("U_E demand/supply identity failed")
        if Fraction(energy["solar_scale"]) * Fraction(energy["raw_reference_mean_j_per_tick"]) != Fraction(energy["target_supply_mean_j_per_tick"]):
            raise SystemExit("solar scale identity failed")
        key = (str(row["taskset_id"]), str(row["target_ue"]))
        material_by_key[(str(row["taskset_id"]), str(rho))] = material
        battery_by_pair[(str(row["taskset_id"]), str(row["target_ue"]), str(rho))] = energy["battery_capacity_j"]

    for taskset_id in taskset_ids:
        reference = None
        for rho in ratios:
            material = material_by_key.get((taskset_id, str(rho)))
            if material is None:
                raise SystemExit("missing rho material pair")
            current = material["E_burst_reference"]
            reference = current if reference is None else reference
            if current != reference:
                raise SystemExit("reference E_burst differs across rho")
    for taskset_id, ue, rho in list(battery_by_pair):
        if battery_by_pair[(taskset_id, ue, str(rho))] != battery_by_pair[(taskset_id, ue, str(experiment.REFERENCE_RATIO))]:
            raise SystemExit("battery is not fixed to rho=2 reference")

    schedulers = list(config["schedulers"])
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in results:
        groups.setdefault((str(row["rho"]), str(row["target_uc"]), str(row["target_ue"])), []).append(row)
    summaries = []
    for (rho, uc, ue), group in sorted(groups.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]), Fraction(item[0][2]))):
        for scheduler in schedulers:
            selected = [row for row in group if row["scheduler"] == scheduler]
            if len(selected) != int(config["samples_per_cell"]):
                raise SystemExit("missing scheduler sample")
            technical = sum(row.get("technical_error") is not None for row in selected)
            total = len(selected)
            summaries.append({
                "rho": rho, "target_uc": uc, "target_ue": ue,
                "scheduler": scheduler, "n_total": total,
                "n_schedulable": sum(row.get("taskset_pass") is True for row in selected),
                "n_deadline_miss": sum(row.get("deadline_miss") is True for row in selected),
                "technical_count": technical,
                "acceptance_ratio": None if technical else sum(row.get("taskset_pass") is True for row in selected) / total,
            })
    write_csv(root / "summary.csv", summaries)
    rho2 = [row for row in summaries if Fraction(row["rho"]) == experiment.REFERENCE_RATIO]
    write_csv(root / "figure_scheduler_priority_energy_uc_rho2.csv", [row for row in rho2])
    write_csv(root / "figure_scheduler_priority_energy_ue_rho2.csv", [row for row in rho2])
    try:
        for fixed_ue in sorted({row["target_ue"] for row in rho2}, key=Fraction):
            rows = [row for row in rho2 if row["target_ue"] == fixed_ue]
            if rows:
                _plot(rows, root / "figure_scheduler_priority_energy_uc_rho2.png", "target_uc", f"rho=2, U_E={fixed_ue}")
                break
        for fixed_uc in sorted({row["target_uc"] for row in rho2}, key=Fraction):
            rows = [row for row in rho2 if row["target_uc"] == fixed_uc]
            if rows:
                _plot(rows, root / "figure_scheduler_priority_energy_ue_rho2.png", "target_ue", f"rho=2, U_C={fixed_uc}")
                break
    except ImportError:
        pass

    control = [row for row in summaries if Fraction(row["rho"]) == 1]
    write_csv(root / "control_rho1.csv", control)
    paired = []
    for (rho, uc, ue), group in sorted(groups.items(), key=lambda item: (Fraction(item[0][0]), Fraction(item[0][1]), Fraction(item[0][2]))):
        block = [row for row in group if row["scheduler"] == "ASAP-BLOCK"]
        nonblock = [row for row in group if row["scheduler"] == "ASAP-NONBLOCK"]
        if len(block) != len(nonblock):
            raise SystemExit("BLOCK/NONBLOCK pairing is incomplete")
        paired.append({
            "rho": rho, "target_uc": uc, "target_ue": ue,
            "n_block": len(block), "n_nonblock": len(nonblock),
            "block_acceptance_ratio": sum(row.get("taskset_pass") is True for row in block) / len(block),
            "nonblock_acceptance_ratio": sum(row.get("taskset_pass") is True for row in nonblock) / len(nonblock),
            "delta_block_minus_nonblock": (
                sum(row.get("taskset_pass") is True for row in block) / len(block)
                - sum(row.get("taskset_pass") is True for row in nonblock) / len(nonblock)
            ),
        })
    write_csv(root / "paired_block_nonblock.csv", paired)

    ordered = sorted(ratios)
    monotonicity = []
    for taskset_id in sorted(taskset_ids):
        high_values = [Fraction(material_by_key[(taskset_id, str(rho))]["high_factor"]) for rho in ordered]
        low_values = [Fraction(material_by_key[(taskset_id, str(rho))]["low_factor"]) for rho in ordered]
        monotonicity.append({
            "taskset_id": taskset_id,
            "high_factor_non_decreasing": all(a <= b for a, b in zip(high_values, high_values[1:])),
            "low_factor_non_increasing": all(a >= b for a, b in zip(low_values, low_values[1:])),
        })
    if not all(row["high_factor_non_decreasing"] and row["low_factor_non_increasing"] for row in monotonicity):
        raise SystemExit("adjacent rho monotonicity failed")
    write_json(root / "monotonicity_report.json", {"rows": monotonicity, "checked": True})
    report = {
        "complete": True, "tasksets": len(tasksets), "requests": len(requests),
        "results": len(results), "duplicate_request_ids": duplicate,
        "missing_request_ids": missing, "unexpected_request_ids": unexpected,
        "rho_values": [str(value) for value in ratios],
        "reference_ratio": str(experiment.REFERENCE_RATIO),
        "control_rows_rho1": len(control), "paired_rows": len(paired),
        "technical_result_count": 0,
        "analysis_workers": analysis_workers,
    }
    (root / "analysis_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--analysis-workers", type=int, default=1)
    args = parser.parse_args(argv)
    report = analyze(args.input, analysis_workers=args.analysis_workers)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
