#!/usr/bin/env python3
"""Read-only, fail-closed acceptance audit for B3-v2 FORMAL evidence.

The audit confirms one pre-registered candidate on an independent taskset
sample.  It never selects, ranks, or modifies parameters, and it never writes
to the output root or taskset store.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_b3_target_trace import (  # noqa: E402
    B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
)
from experiments.v9_3.ext1b_config import (  # noqa: E402
    ext1b_config_hash,
    load_ext1b_config,
)
from experiments.v9_3.ext1b_engine import (  # noqa: E402
    Ext1BRunner,
    assert_ext1b_fair_pairing,
    verify_file_hashes,
)
from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (  # noqa: E402
    CALIBRATION_CLOSURE_FIELDS,
    _boolean,
    _canonical_fraction,
    _csv_rows,
    _integer,
    _json_object,
    _require_fields,
    audit_generation_attempt_history,
    evaluate_primary_numeric_gate,
    summarize_metric_rows,
)


REPORT_SCHEMA = (
    "ASAP_BLOCK_V9_3_EXT1B3_B3_V2_FORMAL_CONFIRMATION_ACCEPTANCE_V1"
)
FROZEN_EXPERIMENT_ID = (
    "asap-block-v9.3-ext1b3-b3-v2-formal-confirmation-r1"
)
FROZEN_SEED_SPACE = (
    "EXT1B3_B3_V2_FORMAL_CONFIRMATION_R1_WORKLOAD_CONTRACT_V3"
)
FROZEN_BASE_SEED = 524843528
FROZEN_BOOTSTRAP_SEED = 1070221135
FROZEN_BOOTSTRAP_RESAMPLES = 2000
FROZEN_UTILIZATIONS = ("1/5", "2/5")
FROZEN_TIMING_CELL_IDS = (
    "positive-slack-energy-available-v2-formal-r1",
    "slack-limited-charging-v2-formal-r1",
)
FROZEN_TIMING_SUBTYPES = (
    "POSITIVE_SLACK_ENERGY_AVAILABLE",
    "SLACK_LIMITED_CHARGING",
)
FROZEN_EXPANDED_TIMING_CELL_IDS = {
    "POSITIVE_SLACK_ENERGY_AVAILABLE": (
        "positive-slack-energy-available-v2-formal-r1"
    ),
    "SLACK_LIMITED_CHARGING": (
        "slack-limited-charging-v2-formal-r1-margin-00-rho-00-eta-00"
    ),
}
FROZEN_TASKSETS_PER_CELL = 200
FROZEN_TASKSET_INDEX_START = 0
FROZEN_STRUCTURAL_RETRY_LIMIT = 24
FROZEN_SCHEDULERS = (
    "gpfp_asap_block",
    "gpfp_alap_block",
    "gpfp_st_block",
)
FROZEN_PARAMETERS = {
    "recovery_margin_ticks": 1,
    "interpolation_rho": "1/2",
    "nominal_energy_supply_ratio": "1/2",
}
FROZEN_SIMULATOR_SHA256 = (
    "77240587c11ad151cd5beb216d7edcb4ac4f5285f9d44ada117e8c2245e5b089"
)
FROZEN_OUTPUT_ROOT = "artifacts/v9_3_ext1b3_b3_v2_formal_confirmation_r1"
FROZEN_TASKSET_STORE = (
    "artifacts/v9_3_ext1b3_b3_v2_formal_confirmation_r1_taskset_store"
)
CALIBRATION_OUTPUT_ROOT = "artifacts/v9_3_ext1b3_b3_v2_full_calibration"
CALIBRATION_TASKSET_STORE = (
    "artifacts/v9_3_ext1b3_b3_v2_full_calibration_taskset_store"
)
EXPECTED_CELL_COUNT = 4
EXPECTED_PAIRED_INSTANCES = 800
EXPECTED_SCHEDULER_REQUESTS = 2400
EXPECTED_FORMAL_UNITS = 4
VALID_TERMINAL_STATUSES = {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}
EXPECTED_SELECTION_POLICY = (
    "structural predicates and runtime activation only; scheduler outcomes "
    "excluded"
)
SEED_DERIVATION_RULE = (
    "1 + big_endian_uint64(sha256(label)[0:8]) mod (2^31 - 1)"
)
BASE_SEED_LABEL = (
    "ASAP-BLOCK/v9.3/EXT-1B/B3/FORMAL-CONFIRMATION-R1/base-seed"
)
BOOTSTRAP_SEED_LABEL = (
    "ASAP-BLOCK/v9.3/EXT-1B/B3/FORMAL-CONFIRMATION-R1/bootstrap-seed"
)


class FormalConfirmationAuditError(RuntimeError):
    """The supplied formal evidence is unreadable or malformed."""


def _project_path(value: Any) -> Path:
    path = Path(str(value))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _failure_report(errors: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "protocol_state": "FORMAL_CONFIRMATION_ONLY",
        "frozen_parameters": dict(FROZEN_PARAMETERS),
        "parameter_selection_permitted": False,
        "calibration_samples_included": False,
        "dataset_integrity": {
            "passed": False,
            "errors": [str(item) for item in errors] or ["unknown audit failure"],
        },
        "formal_gate": {
            "passed": False,
            "dataset_integrity_passed": False,
            "numeric_gate_passed": False,
        },
        "formal_confirmation_passed": False,
        "decision": "FORMAL_CONFIRMATION_FAILED",
        "parameters_may_be_adjusted_from_this_result": False,
        "required_next_action": "RETAIN_EVIDENCE_AND_USE_NEW_PR_AND_NEW_PROTOCOL",
    }


def _frozen_config_audit(
    config: Mapping[str, Any], config_path: Path, output_root: Path,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []

    def require(observed: Any, expected: Any, label: str) -> None:
        if observed != expected:
            errors.append(
                f"{label} mismatch: observed={observed!r}, expected={expected!r}"
            )

    require(config.get("experiment_id"), FROZEN_EXPERIMENT_ID, "experiment_id")
    require(config.get("parameter_status"), "FORMAL", "parameter_status")
    require(config.get("seed_space"), FROZEN_SEED_SPACE, "seed_space")
    require(tuple(config.get("scheduler_ids", ())), FROZEN_SCHEDULERS, "schedulers")
    require(
        tuple(config.get("required_outputs", ())),
        (
            "b3_timing_events.csv",
            "b3_summary.csv",
            "b3_formal_confirmation_summary.csv",
        ),
        "required_outputs",
    )

    grid = config.get("grid", {})
    require(tuple(grid.get("utilization_points", ())), FROZEN_UTILIZATIONS, "utilizations")
    require(grid.get("tasksets_per_cell"), FROZEN_TASKSETS_PER_CELL, "tasksets_per_cell")
    require(grid.get("taskset_index_start"), FROZEN_TASKSET_INDEX_START, "taskset_index_start")
    require(grid.get("base_seed"), FROZEN_BASE_SEED, "base_seed")
    require(grid.get("seed_mode"), "generation_dimensions", "seed_mode")

    statistics = config.get("statistics", {})
    require(statistics.get("bootstrap_seed"), FROZEN_BOOTSTRAP_SEED, "bootstrap_seed")
    require(
        statistics.get("bootstrap_resamples"),
        FROZEN_BOOTSTRAP_RESAMPLES,
        "bootstrap_resamples",
    )

    execution = config.get("execution", {})
    require(execution.get("output_root"), FROZEN_OUTPUT_ROOT, "execution.output_root")
    require(execution.get("taskset_store"), FROZEN_TASKSET_STORE, "execution.taskset_store")
    require(execution.get("resume"), False, "execution.resume")
    configured_output = _project_path(execution.get("output_root"))
    configured_store = _project_path(execution.get("taskset_store"))
    require(configured_output, output_root.resolve(), "audited output_root")
    if configured_output == configured_store:
        errors.append("taskset_store must be distinct from output_root")
    if execution.get("output_root") == CALIBRATION_OUTPUT_ROOT:
        errors.append("FORMAL output_root reuses calibration output")
    if execution.get("taskset_store") == CALIBRATION_TASKSET_STORE:
        errors.append("FORMAL taskset_store reuses calibration store")

    scenario = config.get("scenario", {})
    require(
        scenario.get("scenario_contract_id"),
        B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2,
        "scenario_contract_id",
    )
    require(
        scenario.get("structural_retry_limit"),
        FROZEN_STRUCTURAL_RETRY_LIMIT,
        "structural_retry_limit",
    )
    require(scenario.get("interpolation_rho"), "1/2", "interpolation_rho")
    parameter_grid = scenario.get("calibration_grid", {})
    require(
        tuple(parameter_grid.get("recovery_margin_ticks", ())),
        (1,),
        "frozen recovery margins",
    )
    require(
        tuple(parameter_grid.get("interpolation_rhos", ())),
        ("1/2",),
        "frozen interpolation rhos",
    )
    require(
        tuple(parameter_grid.get("nominal_energy_supply_ratios", ())),
        ("1/2",),
        "frozen nominal supply ratios",
    )
    timing_cells = scenario.get("timing_cells", ())
    require(
        tuple(row.get("id") for row in timing_cells),
        FROZEN_TIMING_CELL_IDS,
        "timing cell IDs",
    )
    require(
        tuple(row.get("subtype") for row in timing_cells),
        FROZEN_TIMING_SUBTYPES,
        "timing cell subtypes",
    )

    simulation = config.get("simulation", {})
    for field, expected in (
        ("horizon", 400),
        ("maximum_horizon", 400),
        ("timeout_seconds", 30),
        ("trace_mode", "semantic"),
        ("retain_trace", True),
        ("simulator_bin", "./build/rtsim/rtsim"),
    ):
        require(simulation.get(field), expected, f"simulation.{field}")

    try:
        description = Ext1BRunner(config).describe()
    except Exception as exc:
        errors.append(f"runner description failed: {exc}")
        description = {}
    for field, expected in (
        ("cell_count", EXPECTED_CELL_COUNT),
        ("tasksets_per_cell", FROZEN_TASKSETS_PER_CELL),
        ("scheduler_count", len(FROZEN_SCHEDULERS)),
        ("paired_instance_count", EXPECTED_PAIRED_INSTANCES),
        ("simulation_request_count", EXPECTED_SCHEDULER_REQUESTS),
    ):
        require(description.get(field), expected, f"runner {field}")

    return errors, {
        "source_config": str(config_path.resolve()),
        "source_config_hash": ext1b_config_hash(config),
        "configured_output_root": str(configured_output),
        "configured_taskset_store": str(configured_store),
        "runner_description": description,
        "seed_derivation": {
            "rule": SEED_DERIVATION_RULE,
            "base_seed_label": BASE_SEED_LABEL,
            "bootstrap_seed_label": BOOTSTRAP_SEED_LABEL,
            "base_seed": FROZEN_BASE_SEED,
            "bootstrap_seed": FROZEN_BOOTSTRAP_SEED,
        },
    }


def audit_dataset_tables(
    config: Mapping[str, Any],
    *,
    checkpoint: Mapping[str, Any],
    metadata: Mapping[str, Any],
    generated: Sequence[Mapping[str, Any]],
    generation_attempts: Sequence[Mapping[str, Any]],
    instances: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    simulation_attempts: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
    b3_rows: Sequence[Mapping[str, Any]],
    formal_rows: Sequence[Mapping[str, Any]],
    terminal_ids: Sequence[str],
    file_hashes_valid: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Audit already-loaded evidence tables without mutating them."""

    errors: list[str] = []
    config_hash = ext1b_config_hash(config)
    expected_checkpoint = {
        "schema": "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1",
        "config_hash": config_hash,
        "requested": EXPECTED_SCHEDULER_REQUESTS,
        "terminal": EXPECTED_SCHEDULER_REQUESTS,
        "pending": 0,
        "stop_requested": False,
    }
    for field, expected in expected_checkpoint.items():
        if checkpoint.get(field) != expected:
            errors.append(f"checkpoint {field} mismatch")

    metadata_expected = {
        "experiment_id": FROZEN_EXPERIMENT_ID,
        "parameter_status": "FORMAL",
        "seed_space": FROZEN_SEED_SPACE,
        "config_hash": config_hash,
        "bootstrap_seed": FROZEN_BOOTSTRAP_SEED,
        "bootstrap_resamples": FROZEN_BOOTSTRAP_RESAMPLES,
        "selection_policy": EXPECTED_SELECTION_POLICY,
        "simulator_build_hash": FROZEN_SIMULATOR_SHA256,
    }
    for field, expected in metadata_expected.items():
        if metadata.get(field) != expected:
            errors.append(f"run_metadata {field} mismatch")
    git_head = str(metadata.get("git_head", ""))
    if re.fullmatch(r"[0-9a-f]{40}", git_head) is None:
        errors.append("run_metadata git_head is not a full commit identity")
    if _project_path(metadata.get("simulator_path")) != _project_path(
        config["simulation"]["simulator_bin"]
    ):
        errors.append("run_metadata simulator_path mismatch")
    target_contract = metadata.get("target_trace_contract", {})
    if not isinstance(target_contract, Mapping) or target_contract.get(
        "scenario_contract_id"
    ) != B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2:
        errors.append("run_metadata target-trace contract mismatch")

    expected_lengths = (
        ("generated tasksets", generated, EXPECTED_PAIRED_INSTANCES),
        ("scenario instances", instances, EXPECTED_PAIRED_INSTANCES),
        ("simulation requests", requests, EXPECTED_SCHEDULER_REQUESTS),
        ("simulation attempts", simulation_attempts, EXPECTED_SCHEDULER_REQUESTS),
        ("simulation results", results, EXPECTED_SCHEDULER_REQUESTS),
        ("B3 summary", b3_rows, EXPECTED_SCHEDULER_REQUESTS),
        ("B3 formal confirmation summary", formal_rows, EXPECTED_FORMAL_UNITS),
    )
    for label, rows, expected in expected_lengths:
        if len(rows) != expected:
            errors.append(f"{label} row count is {len(rows)}, expected {expected}")
    if failures:
        errors.append(f"runner failure table is non-empty ({len(failures)} rows)")

    request_ids = [str(row.get("request_id", "")) for row in requests]
    result_ids = [str(row.get("request_id", "")) for row in results]
    attempt_ids = [str(row.get("request_id", "")) for row in simulation_attempts]
    b3_ids = [str(row.get("request_id", "")) for row in b3_rows]
    terminal_id_list = [str(value) for value in terminal_ids]
    if len(set(request_ids)) != len(request_ids):
        errors.append("simulation_requests contains duplicate request IDs")
    request_set = set(request_ids)
    for label, values in (
        ("simulation_results", result_ids),
        ("simulation_attempts", attempt_ids),
        ("b3_summary", b3_ids),
        ("terminal results", terminal_id_list),
    ):
        if set(values) != request_set or len(values) != len(set(values)):
            errors.append(f"{label} does not form a unique complete request set")

    try:
        assert_ext1b_fair_pairing(requests, FROZEN_SCHEDULERS)
    except (KeyError, RuntimeError) as exc:
        errors.append(f"paired request audit failed: {exc}")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in requests:
        grouped[str(row.get("paired_instance_id", ""))].append(row)
    if len(grouped) != EXPECTED_PAIRED_INSTANCES:
        errors.append("paired instance request group count mismatch")
    if any(
        [str(row.get("scheduler_id", "")) for row in members]
        != list(FROZEN_SCHEDULERS)
        for members in grouped.values()
    ):
        errors.append("paired request scheduler order is incomplete or changed")
    if any(str(row.get("request_status", "")) != "PLANNED" for row in requests):
        errors.append("simulation_requests contains a non-PLANNED request")
    if any(
        str(row.get("simulator_build_hash", "")) != FROZEN_SIMULATOR_SHA256
        for row in requests
    ):
        errors.append("simulation_requests simulator identity mismatch")

    terminal_statuses = Counter(str(row.get("status", "")) for row in results)
    invalid_statuses = sorted(set(terminal_statuses) - VALID_TERMINAL_STATUSES)
    if invalid_statuses:
        errors.append(f"invalid formal terminal statuses: {invalid_statuses}")
    attempt_statuses = Counter(
        str(row.get("status", "")) for row in simulation_attempts
    )
    invalid_attempt_statuses = sorted(
        set(attempt_statuses) - VALID_TERMINAL_STATUSES
    )
    if invalid_attempt_statuses:
        errors.append(
            "runner failures/timeouts in simulation attempts: "
            f"{invalid_attempt_statuses}"
        )

    _require_fields(
        b3_rows,
        (
            "audit_closed", "audit_error_count", "timing_illegal_count",
            "timing_unclassifiable_count",
        ),
        "b3_summary",
    )
    illegal_count = sum(
        _integer(row.get("timing_illegal_count"), "timing_illegal_count")
        for row in b3_rows
    )
    unclassifiable_count = sum(
        _integer(
            row.get("timing_unclassifiable_count"),
            "timing_unclassifiable_count",
        )
        for row in b3_rows
    )
    b3_audit_errors = sum(
        _integer(row.get("audit_error_count"), "audit_error_count")
        for row in b3_rows
    )
    b3_open_count = sum(
        not _boolean(row.get("audit_closed"), "audit_closed")
        for row in b3_rows
    )
    if illegal_count:
        errors.append(f"illegal timing transitions observed: {illegal_count}")
    if unclassifiable_count:
        errors.append(
            f"unclassifiable timing transitions observed: {unclassifiable_count}"
        )
    if b3_audit_errors:
        errors.append(f"B3 timing audit errors observed: {b3_audit_errors}")
    if b3_open_count:
        errors.append(f"B3 timing audits not closed: {b3_open_count}")

    scenario_cell_ids = sorted({
        str(cell[1]["scenario_cell_id"])
        for cell in Ext1BRunner(config).describe()["cells"]
    })
    history_errors, history_report = audit_generation_attempt_history(
        generation_attempts,
        generated,
        instances,
        requests,
        scenario_cell_ids=scenario_cell_ids,
        utilizations=FROZEN_UTILIZATIONS,
        logical_index_start=FROZEN_TASKSET_INDEX_START,
        tasksets_per_unit=FROZEN_TASKSETS_PER_CELL,
        retry_limit=FROZEN_STRUCTURAL_RETRY_LIMIT,
        scheduler_ids=FROZEN_SCHEDULERS,
    )
    errors.extend(history_errors)
    accepted_attempts = [
        row for row in generation_attempts
        if str(row.get("attempt_status", "")) == "ACCEPTED"
    ]
    if len(accepted_attempts) != EXPECTED_PAIRED_INSTANCES:
        errors.append("accepted generation attempt count is not 800")
    if len({row.get("taskset_hash") for row in generated}) != len(generated):
        errors.append("generated taskset hashes are not unique")
    if not file_hashes_valid:
        errors.append("output file hash verification failed")

    return errors, {
        "checkpoint": dict(checkpoint),
        "git_head": git_head,
        "simulator_sha256": str(metadata.get("simulator_build_hash", "")),
        "terminal_status_counts": dict(sorted(terminal_statuses.items())),
        "simulation_attempt_status_counts": dict(sorted(attempt_statuses.items())),
        "runner_failure_count": len(failures),
        "illegal_timing_transition_count": illegal_count,
        "unclassifiable_timing_transition_count": unclassifiable_count,
        "b3_timing_audit_error_count": b3_audit_errors,
        "b3_timing_open_audit_count": b3_open_count,
        "generated_taskset_count": len(generated),
        "generation_attempt_count": len(generation_attempts),
        "accepted_generation_attempt_count": len(accepted_attempts),
        **history_report,
        "paired_instance_count": len(instances),
        "scheduler_request_count": len(requests),
        "terminal_result_count": len(results),
        "formal_unit_count": len(formal_rows),
        "file_hashes_valid": bool(file_hashes_valid),
        "calibration_samples_included": False,
        "sim_deadline_miss_is_schedulability_or_performance_evidence": False,
    }


def _formal_units_audit(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[str] = []
    _require_fields(
        rows,
        (
            "scenario_cell_id", "normalized_utilization", "timing_subtype",
            "configured_recovery_margin_ticks", "interpolation_rho",
            "nominal_energy_supply_ratio",
            "target_recovery_contract_applicable",
            "target_observation_denominator", "structurally_accepted_count",
            "accepted_capacity_infeasible_task_count",
            "accepted_capacity_infeasible_taskset_count",
            *CALIBRATION_CLOSURE_FIELDS,
        ),
        "b3_formal_confirmation_summary",
    )
    charging: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    unit_counts: Counter[tuple[str, str]] = Counter()
    for raw in rows:
        row = dict(raw)
        utilization = _canonical_fraction(
            row.get("normalized_utilization"), "formal utilization"
        )
        subtype = str(row.get("timing_subtype", ""))
        unit_counts[(utilization, subtype)] += 1
        if utilization not in FROZEN_UTILIZATIONS:
            errors.append(f"unexpected formal utilization: {utilization}")
        if row.get("scenario_cell_id") != FROZEN_EXPANDED_TIMING_CELL_IDS.get(
            subtype
        ):
            errors.append("formal scenario cell identity is missing or changed")
        if _integer(
            row.get("structurally_accepted_count"), "structurally accepted"
        ) != FROZEN_TASKSETS_PER_CELL:
            errors.append("formal unit does not contain 200 accepted tasksets")
        if any(
            not _boolean(row.get(field), field)
            for field in CALIBRATION_CLOSURE_FIELDS
        ):
            errors.append("formal unit has an open evidence audit")
        if _integer(
            row.get("accepted_capacity_infeasible_task_count"),
            "capacity-infeasible task count",
        ) != 0 or _integer(
            row.get("accepted_capacity_infeasible_taskset_count"),
            "capacity-infeasible taskset count",
        ) != 0:
            errors.append("formal unit accepted capacity-infeasible evidence")

        applicable = _boolean(
            row.get("target_recovery_contract_applicable"),
            "target recovery applicability",
        )
        denominator = _integer(
            row.get("target_observation_denominator"), "target denominator"
        )
        if subtype == "POSITIVE_SLACK_ENERGY_AVAILABLE":
            controls.append(row)
            if applicable or denominator != 0:
                errors.append("positive control entered the charging gate")
            continue
        if subtype != "SLACK_LIMITED_CHARGING":
            errors.append(f"unknown formal timing subtype: {subtype}")
            continue
        charging.append(row)
        observed_parameters = {
            "recovery_margin_ticks": _integer(
                row.get("configured_recovery_margin_ticks"), "recovery margin"
            ),
            "interpolation_rho": _canonical_fraction(
                row.get("interpolation_rho"), "interpolation rho"
            ),
            "nominal_energy_supply_ratio": _canonical_fraction(
                row.get("nominal_energy_supply_ratio"), "nominal supply ratio"
            ),
        }
        if observed_parameters != FROZEN_PARAMETERS:
            errors.append(
                f"formal evidence contains a non-frozen candidate: {observed_parameters}"
            )
        if not applicable or denominator != FROZEN_TASKSETS_PER_CELL:
            errors.append("charging unit has invalid applicability or denominator")

    for utilization in FROZEN_UTILIZATIONS:
        for subtype in FROZEN_TIMING_SUBTYPES:
            if unit_counts[(utilization, subtype)] != 1:
                errors.append(
                    f"formal unit domain is incomplete for {utilization}/{subtype}"
                )
    if len(charging) != 2 or len(controls) != 2:
        errors.append("formal evidence must contain two charging and two control units")
    return errors, charging, controls


def _audit_formal_confirmation(
    config_path: Path, output_root: Path,
) -> dict[str, Any]:
    config = load_ext1b_config(config_path)
    config_errors, config_report = _frozen_config_audit(
        config, config_path, output_root,
    )
    persisted = load_ext1b_config(output_root / "run_config.yaml")
    if ext1b_config_hash(persisted) != ext1b_config_hash(config):
        config_errors.append(
            "persisted run_config does not match the frozen source config"
        )

    generated = _csv_rows(output_root / "generated_tasksets.csv")
    generation_attempts = _csv_rows(output_root / "generation_attempts.csv")
    instances = _csv_rows(output_root / "scenario_instances.csv")
    requests = _csv_rows(output_root / "simulation_requests.csv")
    simulation_attempts = _csv_rows(output_root / "simulation_attempts.csv")
    results = _csv_rows(output_root / "simulation_results.csv")
    failures = _csv_rows(output_root / "failures.csv")
    b3_rows = _csv_rows(output_root / "b3_summary.csv")
    formal_rows = _csv_rows(
        output_root / "b3_formal_confirmation_summary.csv"
    )
    terminal_root = output_root / "simulation_terminal_results"
    terminal_ids = (
        [path.stem for path in terminal_root.glob("*.json")]
        if terminal_root.is_dir()
        else []
    )
    dataset_errors, dataset_report = audit_dataset_tables(
        config,
        checkpoint=_json_object(output_root / "checkpoint.json"),
        metadata=_json_object(output_root / "run_metadata.json"),
        generated=generated,
        generation_attempts=generation_attempts,
        instances=instances,
        requests=requests,
        simulation_attempts=simulation_attempts,
        results=results,
        failures=failures,
        b3_rows=b3_rows,
        formal_rows=formal_rows,
        terminal_ids=terminal_ids,
        file_hashes_valid=verify_file_hashes(output_root),
    )
    unit_errors, charging_rows, control_rows = _formal_units_audit(formal_rows)
    integrity_errors = config_errors + dataset_errors + unit_errors
    integrity_passed = not integrity_errors

    per_utilization: dict[str, Any] = {}
    for utilization in FROZEN_UTILIZATIONS:
        selected = [
            row for row in charging_rows
            if _canonical_fraction(
                row.get("normalized_utilization"), "charging utilization"
            ) == utilization
        ]
        per_utilization[utilization] = evaluate_primary_numeric_gate(
            selected, expected_denominator=FROZEN_TASKSETS_PER_CELL,
        )
    overall = evaluate_primary_numeric_gate(
        charging_rows,
        expected_denominator=(
            len(FROZEN_UTILIZATIONS) * FROZEN_TASKSETS_PER_CELL
        ),
    )
    numeric_passed = (
        all(value["passed"] for value in per_utilization.values())
        and overall["passed"]
    )
    formal_passed = integrity_passed and numeric_passed
    decision = (
        "FORMAL_CONFIRMATION_PASSED"
        if formal_passed
        else "FORMAL_CONFIRMATION_FAILED"
    )
    return {
        "schema": REPORT_SCHEMA,
        "protocol_state": "FORMAL_CONFIRMATION_ONLY",
        "frozen_parameters": dict(FROZEN_PARAMETERS),
        "parameter_selection_permitted": False,
        "calibration_samples_included": False,
        "config_audit": config_report,
        "dataset_integrity": {
            "passed": integrity_passed,
            "errors": integrity_errors,
            **dataset_report,
        },
        "positive_controls": [
            {
                "normalized_utilization": _canonical_fraction(
                    row.get("normalized_utilization"), "control utilization"
                ),
                "metrics": summarize_metric_rows([row]),
            }
            for row in sorted(
                control_rows,
                key=lambda item: _canonical_fraction(
                    item.get("normalized_utilization"), "control utilization"
                ),
            )
        ],
        "formal_gate": {
            "passed": formal_passed,
            "dataset_integrity_passed": integrity_passed,
            "numeric_gate_passed": numeric_passed,
            "per_utilization": per_utilization,
            "overall": overall,
        },
        "formal_confirmation_passed": formal_passed,
        "decision": decision,
        "parameters_may_be_adjusted_from_this_result": False,
        "required_next_action": (
            "RETAIN_EVIDENCE_AND_REPORT_FROZEN_PROFILE_CONFIRMATION"
            if formal_passed
            else "RETAIN_EVIDENCE_AND_USE_NEW_PR_AND_NEW_PROTOCOL"
        ),
    }


def audit_formal_confirmation(
    config_path: Path, output_root: Path,
) -> dict[str, Any]:
    """Return a structured failure instead of trusting partial evidence."""

    try:
        return _audit_formal_confirmation(config_path, output_root.resolve())
    except Exception as exc:
        return _failure_report([str(exc)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            PROJECT_ROOT
            / "configs/v9_3_ext1b3_b3_v2_formal_confirmation_r1.yaml"
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = audit_formal_confirmation(args.config, args.output_root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report.get("formal_confirmation_passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
