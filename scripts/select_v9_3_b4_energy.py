#!/usr/bin/env python3
"""Select B4 energy conditions using only preregistered Q values."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.config import domain_hash
from experiments.v9_3.performance_calibration import resolve_30s_confirmation, select_calibration
from experiments.v9_3.performance_identity import calibration_selection_identity
from experiments.v9_3.result_writer import atomic_write_json


def _rows(path: Path, horizon_ms: int) -> list:
    if path.suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value["results"] if isinstance(value, dict) else value
    else:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    for row in rows:
        row["observed_pass"] = str(row["observed_pass"]).lower() in {"1", "true", "yes"}
    return [
        row for row in rows
        if int(row.get("runtime_horizon_ms", horizon_ms)) == horizon_ms
    ]


def _write_cells(path: Path, rows: list) -> None:
    fields = ("kappa", "eta", "u_norm", "Q", "scheduler_pass_ratios", "tasksets_per_scheduler")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = dict(row)
            value["scheduler_pass_ratios"] = json.dumps(value["scheduler_pass_ratios"], separators=(",", ":"))
            writer.writerow({field: value[field] for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-10s", type=Path, required=True)
    parser.add_argument("--results-30s", type=Path)
    parser.add_argument("--full-grid-results-30s", type=Path)
    parser.add_argument("--request-plan", type=Path, required=True)
    parser.add_argument("--taskset-manifest", type=Path, required=True)
    parser.add_argument("--extension-already-used", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = select_calibration(
        _rows(args.results_10s, 10000),
        extension_already_used=args.extension_already_used,
    )
    plan = json.loads(args.request_plan.read_text(encoding="utf-8"))
    manifest = json.loads(args.taskset_manifest.read_text(encoding="utf-8"))
    q_values = list(selection.q_values)
    grid = sorted(
        {(str(row["kappa"]), str(row["eta"])) for row in q_values},
        key=lambda pair: (__import__("fractions").Fraction(pair[0]), __import__("fractions").Fraction(pair[1])),
    )
    energy_rows = [request.get("energy_material", {}) for request in plan.get("requests", [])]
    solar_hashes = sorted({str(row.get("solar_source_hash")) for row in energy_rows if row.get("solar_source_hash")})
    system_hashes = sorted({str(row.get("system_template_hash")) for row in energy_rows if row.get("system_template_hash")})
    power_hashes = sorted({str(entry.get("power_hash")) for entry in manifest.get("entries", [])})
    document = {
        "schema": "ASAP_BLOCK_V9_3_B4_CALIBRATION_SELECTION_V1",
        "version": 1,
        **selection.document(),
        "initial_grid": {
            "kappa": ["10", "50", "200"],
            "eta": ["1/2", "3/4", "1", "5/4", "3/2"],
        },
        "final_grid": [{"kappa": kappa, "eta": eta} for kappa, eta in grid],
        "result_10s": selection.document(),
        "fallback_full_30s_grid_used": False,
        "taskset_store_identity": manifest.get("store_identity"),
        "source_commit": plan.get("source_commit"),
        "simulator_binary_sha256": plan.get("simulator_binary_sha256"),
        "system_template_hash": system_hashes[0] if len(system_hashes) == 1 else None,
        "solar_source_hash": solar_hashes[0] if len(solar_hashes) == 1 else None,
        "power_contract_hash": domain_hash(
            "ASAP_BLOCK:V9.3:B4:CAL_POWER_CONTRACT_SET:v1", power_hashes,
        ),
        "config_hash": plan.get("config_hash"),
    }
    if args.results_30s is not None and selection.status == "SELECTED":
        resolved = resolve_30s_confirmation(
            selection, _rows(args.results_30s, 30000),
            full_grid_rows=(
                _rows(args.full_grid_results_30s, 30000)
                if args.full_grid_results_30s is not None else None
            ),
        )
        final_selection = resolved["selection"]
        document.update(final_selection.document())
        document["result_30s"] = resolved["confirmation"]
        document["confirmation_30s"] = resolved["confirmation"]
        document["fallback_full_30s_grid_used"] = resolved["fallback_full_30s_grid_used"]
        document["confirmation_status"] = resolved["status"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_cells(args.output.parent / "calibration_cells.csv", list(document["q_values"]))
    document["selection_identity"] = calibration_selection_identity(document)
    atomic_write_json(args.output, document)
    print(json.dumps(document, sort_keys=True))
    selected = document.get("status") == "SELECTED"
    confirmed = args.results_30s is not None and document.get("confirmation_status") == "CONFIRMED"
    provenance = all(document.get(key) for key in (
        "taskset_store_identity", "source_commit", "simulator_binary_sha256",
        "system_template_hash", "solar_source_hash", "power_contract_hash", "config_hash",
    ))
    return 0 if selected and confirmed and provenance else 2


if __name__ == "__main__":
    raise SystemExit(main())
