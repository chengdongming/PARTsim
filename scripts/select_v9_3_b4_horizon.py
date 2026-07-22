#!/usr/bin/env python3
"""Create the three-state B4 horizon selection seal from complete gate results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.performance_audit import load_terminal_results
from experiments.v9_3.performance_horizon_gate import decide_horizon_gate, gate_rows_from_terminal
from experiments.v9_3.performance_identity import horizon_selection_identity
from experiments.v9_3.result_writer import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    terminal = load_terminal_results(args.results)
    rows, closure = gate_rows_from_terminal(plan, terminal)
    decision = decide_horizon_gate(rows)
    selected = sorted(
        str(request["semantic_request_id"]) for request in plan.get("requests", [])
        if int(request["runtime_horizon_ms"]) == decision.selected_horizon_ms
    ) if decision.selected_horizon_ms else []
    unselected = sorted(
        str(request["semantic_request_id"]) for request in plan.get("requests", [])
        if decision.selected_horizon_ms and int(request["runtime_horizon_ms"]) != decision.selected_horizon_ms
    )
    document = {
        "schema": "ASAP_BLOCK_V9_3_B4_HORIZON_SELECTION_V1",
        **decision.document(), "gate_closure": closure,
        "gate_plan_identity": plan.get("formal_plan_identity"),
        "source_commit": plan.get("source_commit"),
        "simulator_binary_sha256": plan.get("simulator_binary_sha256"),
        "selected_gate_request_ids": selected,
        "unselected_gate_request_ids": unselected,
    }
    document["horizon_selection_identity"] = horizon_selection_identity(document)
    atomic_write_json(args.output, document)
    print(json.dumps(document, sort_keys=True))
    return 0 if decision.state in {"SELECT_30S", "SELECT_60S"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
