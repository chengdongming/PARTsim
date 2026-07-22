#!/usr/bin/env python3
"""Audit authoritative CAL plans/results/store, then apply the Q-only selector."""

from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.config import domain_hash
from experiments.v9_3.performance_audit import load_terminal_results
from experiments.v9_3.performance_calibration import (
    resolve_30s_confirmation, resolve_branch_a_extension, select_calibration,
)
from experiments.v9_3.performance_calibration_audit import audit_calibration_phase
from experiments.v9_3.performance_config import load_performance_config
from experiments.v9_3.performance_environment import (
    StageEnvironmentError, assert_environment_compatible, build_stage_environment,
)
from experiments.v9_3.performance_identity import calibration_selection_identity
from experiments.v9_3.performance_taskset_store import PerformanceTasksetStore
from experiments.v9_3.result_writer import atomic_write_json


CAL_AUDIT_SET_DOMAIN = "ASAP_BLOCK:V9.3:B4:CAL_AUDIT_SET:v1"


def _write_cells(path: Path, rows: list) -> None:
    fields = ("kappa", "eta", "u_norm", "Q", "scheduler_pass_ratios", "tasksets_per_scheduler")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = dict(row)
            value["scheduler_pass_ratios"] = json.dumps(value["scheduler_pass_ratios"], separators=(",", ":"))
            writer.writerow({field: value[field] for field in fields})


def _load_plan(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("requests"), list):
        raise ValueError(f"invalid CAL phase plan: {path}")
    return value


def _invalid_document(reason: str, audits: list) -> dict:
    return {
        "schema": "ASAP_BLOCK_V9_3_B4_CALIBRATION_SELECTION_V1",
        "version": 1, "status": "CAL_INVALID", "reason": reason,
        "phase_audits": [audit.document() for audit in audits],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--initial-plan", type=Path, required=True)
    parser.add_argument("--extension-plan", type=Path)
    parser.add_argument("--confirmation-plan", type=Path)
    parser.add_argument("--full-grid-plan", type=Path)
    parser.add_argument("--terminal-results", type=Path, required=True)
    parser.add_argument("--taskset-manifest", type=Path, required=True)
    parser.add_argument("--simulator-bin", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = load_performance_config(args.config)
    if config["stage"] != "CALIBRATION":
        parser.error("energy selection requires a CALIBRATION config")
    if args.simulator_bin is not None:
        config["simulation"]["simulator_bin"] = str(args.simulator_bin.resolve())
    try:
        current_environment = build_stage_environment(config, project_root=PROJECT_ROOT)
    except StageEnvironmentError as exc:
        parser.error(str(exc))
    store = PerformanceTasksetStore(args.taskset_manifest.parent, config)
    manifest = store.verify_manifest()
    supplied_manifest = json.loads(args.taskset_manifest.read_text(encoding="utf-8"))
    if supplied_manifest.get("store_identity") != manifest.get("store_identity"):
        parser.error("supplied CAL manifest differs from verified frozen store")

    phase_paths = [("initial", args.initial_plan)]
    if args.extension_plan is not None:
        phase_paths.append(("extension", args.extension_plan))
    if args.confirmation_plan is not None:
        phase_paths.append(("confirmation", args.confirmation_plan))
    if args.full_grid_plan is not None:
        phase_paths.append(("confirmation_full_grid", args.full_grid_plan))
    plans = {name: _load_plan(path) for name, path in phase_paths}
    all_results = load_terminal_results(args.terminal_results)
    planned_union = {
        str(request["semantic_request_id"])
        for plan in plans.values() for request in plan["requests"]
    }
    observed_ids = [str(result.get("semantic_request_id", "")) for result in all_results]
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != planned_union:
        document = _invalid_document("CAL phase-plan union does not equal terminal result IDs", [])
        atomic_write_json(args.output, document)
        print(json.dumps(document, sort_keys=True))
        return 2
    try:
        for plan in plans.values():
            assert_environment_compatible(
                current_environment, plan["stage_environment"], require_stage_config=True,
            )
    except (KeyError, StageEnvironmentError) as exc:
        document = _invalid_document(f"CAL stage environment mismatch: {exc}", [])
        atomic_write_json(args.output, document)
        print(json.dumps(document, sort_keys=True))
        return 2

    results_by_id = {str(result["semantic_request_id"]): result for result in all_results}
    audits = []

    def run_audit(label: str, audit_phase: str):
        plan = plans[label]
        rows = [results_by_id[str(request["semantic_request_id"])] for request in plan["requests"]]
        audit = audit_calibration_phase(audit_phase, plan, rows, manifest)
        audits.append(audit)
        return audit

    initial_audit = run_audit("initial", "initial")
    if initial_audit.status != "CAL_VALID":
        document = _invalid_document("initial CAL closure failed", audits)
        atomic_write_json(args.output, document)
        print(json.dumps(document, sort_keys=True))
        return 2
    selection = select_calibration(initial_audit.audited_rows)
    combined_10s_rows = list(initial_audit.audited_rows)
    if "extension" in plans:
        if selection.extension_branch not in {"A", "B"}:
            document = _invalid_document("extension plan supplied without a preregistered extension", audits)
            atomic_write_json(args.output, document)
            print(json.dumps(document, sort_keys=True))
            return 2
        extension_phase = "extension_a" if selection.extension_branch == "A" else "extension_b"
        extension_audit = run_audit("extension", extension_phase)
        if extension_audit.status != "CAL_VALID":
            document = _invalid_document("CAL extension closure failed", audits)
            atomic_write_json(args.output, document)
            print(json.dumps(document, sort_keys=True))
            return 2
        if selection.extension_branch == "A":
            selection = resolve_branch_a_extension(
                selection, initial_audit.audited_rows, extension_audit.audited_rows,
            )
        else:
            combined_10s_rows.extend(extension_audit.audited_rows)
            selection = select_calibration(combined_10s_rows, extension_already_used=True)

    q_values = list(selection.q_values)
    grid = sorted(
        {(str(row["kappa"]), str(row["eta"])) for row in q_values},
        key=lambda pair: (Fraction(pair[0]), Fraction(pair[1])),
    )
    audit_documents = [audit.document() for audit in audits]
    document = {
        "schema": "ASAP_BLOCK_V9_3_B4_CALIBRATION_SELECTION_V1",
        "version": 1, **selection.document(),
        "initial_grid": {
            "kappa": ["10", "50", "200"],
            "eta": ["1/2", "3/4", "1", "5/4", "3/2"],
        },
        "final_grid": [{"kappa": kappa, "eta": eta} for kappa, eta in grid],
        "result_10s": selection.document(),
        "fallback_full_30s_grid_used": False,
        "taskset_store_identity": manifest["store_identity"],
        "source_commit": current_environment["exact_source_commit"],
        "simulator_binary_sha256": current_environment["simulator_binary_sha256"],
        "system_template_hash": current_environment["system_template_sha256"],
        "solar_source_hash": current_environment["solar_data_sha256"],
        "power_contract_hash": current_environment["workload_power_contract_identity"],
        "config_hash": current_environment["stage_config_hash"],
        "stage_environment": current_environment,
        "phase_audits": audit_documents,
    }

    if "confirmation" in plans and selection.status == "SELECTED":
        confirmation_audit = run_audit("confirmation", "confirmation")
        if confirmation_audit.status != "CAL_VALID":
            document = _invalid_document("30-second CAL confirmation closure failed", audits)
            atomic_write_json(args.output, document)
            print(json.dumps(document, sort_keys=True))
            return 2
        full_rows = None
        if "confirmation_full_grid" in plans:
            full_audit = run_audit("confirmation_full_grid", "confirmation_full_grid")
            if full_audit.status != "CAL_VALID":
                document = _invalid_document("full-grid 30-second CAL closure failed", audits)
                atomic_write_json(args.output, document)
                print(json.dumps(document, sort_keys=True))
                return 2
            full_rows = full_audit.audited_rows
        resolved = resolve_30s_confirmation(
            selection, confirmation_audit.audited_rows, full_grid_rows=full_rows,
        )
        final_selection = resolved["selection"]
        document.update(final_selection.document())
        document["result_30s"] = resolved["confirmation"]
        document["confirmation_30s"] = resolved["confirmation"]
        document["fallback_full_30s_grid_used"] = resolved["fallback_full_30s_grid_used"]
        document["confirmation_status"] = resolved["status"]

    document["phase_audits"] = [audit.document() for audit in audits]
    document["calibration_audit_identity"] = domain_hash(
        CAL_AUDIT_SET_DOMAIN, [audit.audit_identity for audit in audits],
    )
    document["request_counters"] = {
        audit.phase: {
            "planned": audit.planned_requests, "observed": audit.observed_results,
            **dict(audit.counters),
        }
        for audit in audits
    }
    document["paired_counts"] = {
        audit.phase: list(audit.paired_counts) for audit in audits
    }
    document["terminal_result_set_identities"] = {
        audit.phase: audit.terminal_result_set_identity for audit in audits
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_cells(args.output.parent / "calibration_cells.csv", list(document["q_values"]))
    document["selection_identity"] = calibration_selection_identity(document)
    atomic_write_json(args.output, document)
    print(json.dumps(document, sort_keys=True))
    selected = document.get("status") == "SELECTED"
    confirmed = document.get("confirmation_status") == "CONFIRMED"
    audits_valid = all(audit.status == "CAL_VALID" for audit in audits)
    return 0 if selected and confirmed and audits_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
