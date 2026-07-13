"""EXT-4 paired/stratified aggregation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict

from .result_writer import read_csv, write_csv


PAIRED_COLUMNS = (
    "base_sample_id", "derived_sample_id", "changed_axis", "pairing_type",
    "method", "base_status", "derived_status", "base_proven",
    "derived_proven", "paired_gain_loss", "base_simulation_status",
    "derived_simulation_status",
)
SUMMARY_COLUMNS = (
    "changed_axis", "level", "method", "rta_requested_denominator",
    "rta_terminal_denominator", "certified_count", "certification_ratio",
    "simulation_requested_denominator", "simulation_valid_denominator",
    "simulation_pass_count", "simulation_pass_ratio_valid", "timeout_count",
    "soundness_rta_pass_sim_fail", "dominance_violation_count",
)
PLOT_COLUMNS = (
    "plot", "changed_axis", "level", "method", "sample_id", "category",
    "x", "y",
)


def _truth(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _ratio(numerator: int, denominator: int) -> Any:
    return numerator / denominator if denominator else "UNAVAILABLE"


def aggregate_ext4(root: Path) -> Dict[str, int]:
    cells = read_csv(root / "robustness_cells.csv")
    samples = read_csv(root / "base_and_derived_samples.csv")
    rta = read_csv(root / "rta_results.csv")
    simulations = read_csv(root / "simulation_results.csv")
    by_rta = {(row["sample_id"], row["method"]): row for row in rta}
    by_sim = {row["sample_id"]: row for row in simulations}
    paired = []
    for sample in samples:
        if not sample.get("base_sample_id") or sample["sample_id"] == sample["base_sample_id"]:
            continue
        base_id, derived_id = sample["base_sample_id"], sample["sample_id"]
        for method in sorted({row["method"] for row in rta}):
            base, derived = by_rta.get((base_id, method)), by_rta.get((derived_id, method))
            if base is None or derived is None:
                continue
            base_proven, derived_proven = _truth(base["taskset_proven"]), _truth(derived["taskset_proven"])
            gain_loss = "TIE"
            if derived_proven and not base_proven:
                gain_loss = "GAIN"
            elif base_proven and not derived_proven:
                gain_loss = "LOSS"
            paired.append({
                "base_sample_id": base_id, "derived_sample_id": derived_id,
                "changed_axis": sample["changed_axis"], "pairing_type": sample["pairing_type"],
                "method": method, "base_status": base["solver_status"],
                "derived_status": derived["solver_status"],
                "base_proven": base_proven, "derived_proven": derived_proven,
                "paired_gain_loss": gain_loss,
                "base_simulation_status": by_sim.get(base_id, {}).get("status", "UNAVAILABLE"),
                "derived_simulation_status": by_sim.get(derived_id, {}).get("status", "UNAVAILABLE"),
            })
    summaries = []
    plots = []
    for cell in cells:
        axis, level = cell["changed_axis"], cell["level"]
        sample_ids = {row["sample_id"] for row in samples if row["cell_id"] == cell["cell_id"]}
        for method in sorted({row["method"] for row in rta}):
            rta_rows = [row for row in rta if row["sample_id"] in sample_ids and row["method"] == method]
            sim_rows = [row for row in simulations if row["sample_id"] in sample_ids]
            valid_sim = [row for row in sim_rows if row["status"] in {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}]
            certified = sum(_truth(row["taskset_proven"]) for row in rta_rows)
            sim_pass = sum(row["status"] == "SIM_PASS_OBSERVED" for row in sim_rows)
            p0 = sum(_truth(row.get("p0_rta_pass_sim_fail")) for row in rta_rows)
            dominance = sum(_truth(row.get("dominance_violation")) for row in rta_rows)
            summaries.append({
                "changed_axis": axis, "level": level, "method": method,
                "rta_requested_denominator": len(rta_rows),
                "rta_terminal_denominator": len(rta_rows),
                "certified_count": certified,
                "certification_ratio": _ratio(certified, len(rta_rows)),
                "simulation_requested_denominator": len(sim_rows),
                "simulation_valid_denominator": len(valid_sim),
                "simulation_pass_count": sim_pass,
                "simulation_pass_ratio_valid": _ratio(sim_pass, len(valid_sim)),
                "timeout_count": sum(row["solver_status"] == "TIMEOUT" for row in rta_rows),
                "soundness_rta_pass_sim_fail": p0,
                "dominance_violation_count": dominance,
            })
            for row in rta_rows:
                plots.append({
                    "plot": "certification", "changed_axis": axis,
                    "level": level, "method": method, "sample_id": row["sample_id"],
                    "category": row["solver_status"], "x": level,
                    "y": 1 if _truth(row["taskset_proven"]) else 0,
                })
                plots.append({
                    "plot": "runtime", "changed_axis": axis,
                    "level": level, "method": method, "sample_id": row["sample_id"],
                    "category": row["solver_status"], "x": level,
                    "y": row["runtime_seconds"],
                })
    write_csv(root / "paired_robustness_results.csv", PAIRED_COLUMNS, paired)
    write_csv(root / "robustness_summary.csv", SUMMARY_COLUMNS, summaries)
    write_csv(root / "ext4_plot_data.csv", PLOT_COLUMNS, plots)
    return {"paired_rows": len(paired), "summary_rows": len(summaries), "plot_rows": len(plots)}
