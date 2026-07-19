#!/usr/bin/env python3
"""Read-only aggregate regression for the retired B2 R1 corpus.

No blind map is opened.  The script reproduces the eight frozen R1 failure
counters, then audits all retained ASAP-SYNC decisions with the R2 contract.
Only aggregate counts are emitted.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from experiments.v9_3.ext1b_b2_batch_audit_r2 import (
    ALLOWED_STATES,
    CONTROL_STATUS_ELIGIBLE_MATCHED_STATE,
    CONTROL_STATUS_NOT_APPLICABLE,
    audit_asap_block_pair_trace,
    audit_asap_sync_trace,
    summarize_b2_observations,
)


R1_FAILURE_EXPECTED = {
    "direct_unclassifiable": 14920,
    "no_individual_affordable": 14913,
    "continuation_precision_mismatch": 7,
    "positive_prefix_control_failure": 36889,
    "invalid_legacy_block_association": 17164,
    "q0_legacy_block": 2251,
    "illegal_partial_launch": 0,
    "synchronization_wait_ticks_mismatch": 0,
    "scheduler_decision_rows": 57500,
    "sync_traces": 144,
}

R2_CLOSURE_EXPECTED = {
    "decision_row_count": 57500,
    "sync_trace_count": 144,
    "state_unclassifiable_count": 0,
    "illegal_partial_count": 0,
    "illegal_transition_count": 0,
    "no_affordable_member_count": 14913,
    "legacy_block_nonatomic_count": 17164,
    "q0_legacy_block_count": 2251,
    "precision_mismatch_count": 0,
    "legacy_precision_recovered_count": 7,
    "continuation_evidence_failure_count": 0,
    "synchronization_wait_ticks_mismatch_count": 0,
    "affordable_atomic_launch_count": 2794,
    "atomic_wait_with_affordable_member_count": 37221,
    "active_batch_opportunity_count": 40015,
    "atomic_wait_share": "37221/40015",
    "control_rows": 37221,
    "matched_control_count": 144,
    "matched_control_failure_count": 0,
    "control_not_applicable_count": 37077,
    "control_evidence_incomplete_count": 0,
    "matched_t0_control_count": 144,
    "matched_t0_control_pass_count": 144,
    "not_applicable_tick_range": [1, 399],
}


class R1RegressionError(RuntimeError):
    """The read-only R1 regression did not match its frozen aggregate."""


def _jsonl(path: Path) -> list[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise R1RegressionError("R1 evidence row must be an object")
            rows.append(value)
    return rows


def _cells(root: Path) -> list[Path]:
    cells = sorted(
        path for path in root.iterdir()
        if path.is_dir() and path.name.startswith("B2R1-")
    )
    if len(cells) != 12:
        raise R1RegressionError("R1 diagnostic corpus must contain 12 cells")
    return cells


def reproduce_r1_failures(root: Path, evidence_root: Path) -> Dict[str, Any]:
    rows = _jsonl(evidence_root / "b2_decision_audit.jsonl")
    controls = _jsonl(evidence_root / "asap_block_positive_prefix_controls.jsonl")
    observed = {
        "direct_unclassifiable": sum(
            row.get("classified_state") == "B2_STATE_UNCLASSIFIABLE" for row in rows
        ),
        "no_individual_affordable": sum(
            row.get("classified_state") == "B2_STATE_UNCLASSIFIABLE"
            and row.get("whole_batch_affordable") is False
            and row.get("feasible_subset_exists") is False
            and not row.get("classification_errors")
            for row in rows
        ),
        "continuation_precision_mismatch": sum(
            row.get("classification_errors") == [
                "candidate_wait_residual_energy_after_continuation_reservation_mJ_mismatch"
            ]
            for row in rows
        ),
        "positive_prefix_control_failure": sum(
            row.get("control_passed") is not True for row in controls
        ),
        "invalid_legacy_block_association": sum(
            row.get("sync_batch_block_present") is True
            and row.get("classified_state")
            != "B2_STATE_BATCH_UNAFFORDABLE_ATOMIC_WAIT"
            for row in rows
        ),
        "q0_legacy_block": sum(
            row.get("sync_batch_block_present") is True
            and int(row.get("candidate_count", -1)) == 0
            for row in rows
        ),
        "illegal_partial_launch": sum(
            row.get("classified_state") == "B2_STATE_ILLEGAL_PARTIAL_LAUNCH"
            for row in rows
        ),
    }
    wait_mismatches = 0
    sync_traces = 0
    for cell in _cells(root):
        with (cell / "simulation_requests.csv").open(
            newline="", encoding="utf-8",
        ) as handle:
            requests = list(csv.DictReader(handle))
        with (cell / "simulation_results.csv").open(
            newline="", encoding="utf-8",
        ) as handle:
            results = {row["request_id"]: row for row in csv.DictReader(handle)}
        for request in requests:
            if request["scheduler_id"] != "gpfp_asap_sync":
                continue
            sync_traces += 1
            request_id = request["request_id"]
            document = json.loads(
                (cell / "retained_traces" / f"{request_id}.json").read_text(
                    encoding="utf-8",
                )
            )
            raw_blocks = sum(
                event.get("event_type") == "sync_batch_block"
                and event.get("scheduler") == "ASAP-Sync"
                for event in document["events"]
            )
            if raw_blocks != int(results[request_id]["synchronization_wait_ticks"]):
                wait_mismatches += 1
    observed.update({
        "synchronization_wait_ticks_mismatch": wait_mismatches,
        "scheduler_decision_rows": len(rows),
        "sync_traces": sync_traces,
    })
    if observed != R1_FAILURE_EXPECTED:
        raise R1RegressionError("B2_R2_FAILURE_REPRODUCTION_MISMATCH")
    return observed


def audit_r2_closure(root: Path) -> Dict[str, Any]:
    all_rows: list[Mapping[str, Any]] = []
    all_controls: list[Mapping[str, Any]] = []
    per_trace_wait_mismatch = 0
    sync_trace_count = 0
    for cell in _cells(root):
        with (cell / "simulation_requests.csv").open(
            newline="", encoding="utf-8",
        ) as handle:
            requests = list(csv.DictReader(handle))
        with (cell / "simulation_results.csv").open(
            newline="", encoding="utf-8",
        ) as handle:
            results = {row["request_id"]: row for row in csv.DictReader(handle)}
        by_pair: Dict[str, Dict[str, Dict[str, str]]] = {}
        for request in requests:
            by_pair.setdefault(request["paired_instance_id"], {})[
                request["scheduler_id"]
            ] = request
        for pair_id, members in sorted(by_pair.items()):
            sync = members["gpfp_asap_sync"]
            block = members["gpfp_asap_block"]
            sync_path = cell / "retained_traces" / f"{sync['request_id']}.json"
            block_path = cell / "retained_traces" / f"{block['request_id']}.json"
            rows = audit_asap_sync_trace(
                sync_path, processors=4, pair_id=pair_id,
            )
            controls = audit_asap_block_pair_trace(
                rows, block_path, processors=4, expected_min_prefix_length=1,
            )
            sync_trace_count += 1
            raw_blocks = sum(row.get("sync_batch_block_present") is True for row in rows)
            reported = int(results[sync["request_id"]]["synchronization_wait_ticks"])
            per_trace_wait_mismatch += int(raw_blocks != reported)
            all_rows.extend(rows)
            all_controls.extend(controls)
    summary = summarize_b2_observations(all_rows, all_controls)
    summary["synchronization_wait_ticks_mismatch_count"] = per_trace_wait_mismatch
    summary.update({
        "sync_trace_count": sync_trace_count,
        "control_rows": len(all_controls),
        "matched_control_count": sum(
            row.get("control_status") == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
            for row in all_controls
        ),
        "matched_t0_control_count": sum(
            row.get("control_status") == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
            and int(row["tick"]) == 0 for row in all_controls
        ),
        "matched_t0_control_pass_count": sum(
            row.get("control_status") == CONTROL_STATUS_ELIGIBLE_MATCHED_STATE
            and int(row["tick"]) == 0 and row.get("control_passed") is True
            for row in all_controls
        ),
        "not_applicable_tick_range": [
            min(int(row["tick"]) for row in all_controls
                if row.get("control_status") == CONTROL_STATUS_NOT_APPLICABLE),
            max(int(row["tick"]) for row in all_controls
                if row.get("control_status") == CONTROL_STATUS_NOT_APPLICABLE),
        ],
    })
    states = Counter(row.get("classified_state") for row in all_rows)
    if set(states) - set(ALLOWED_STATES) or sum(states.values()) != len(all_rows):
        raise R1RegressionError("R2 state partition is not unique and exhaustive")
    observed = {key: summary.get(key) for key in R2_CLOSURE_EXPECTED}
    if observed != R2_CLOSURE_EXPECTED:
        raise R1RegressionError("B2_R2_STATE_CLOSURE_MISMATCH")
    return observed


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--r1-root", type=Path, required=True)
    parser.add_argument("--r1-evidence-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    failures = reproduce_r1_failures(args.r1_root, args.r1_evidence_root)
    closure = audit_r2_closure(args.r1_root)
    print(json.dumps({
        "schema": "EXT1B_B2_R2_R1_READ_ONLY_REGRESSION_V1",
        "status": "PASSED",
        "r1_failure_reproduction": failures,
        "r2_state_closure": closure,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
