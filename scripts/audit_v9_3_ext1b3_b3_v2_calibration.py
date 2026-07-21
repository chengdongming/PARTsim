#!/usr/bin/env python3
"""Read-only, fail-closed acceptance audit for the full B3-v2 calibration.

The script never edits the calibration output or taskset store.  Its JSON
report is written to stdout so that evidence storage remains an explicit
caller action.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.v9_3.ext1b_config import (  # noqa: E402
    ext1b_config_hash,
    load_ext1b_config,
)
from experiments.v9_3.ext1b_engine import (  # noqa: E402
    Ext1BRunner,
    assert_ext1b_fair_pairing,
    verify_file_hashes,
)


REPORT_SCHEMA = "ASAP_BLOCK_V9_3_EXT1B3_B3_V2_CALIBRATION_ACCEPTANCE_V1"
FROZEN_EXPERIMENT_ID = "asap-block-v9.3-ext1b3-b3-v2-full-calibration"
FROZEN_SEED_SPACE = "EXT1B3_TARGET_TRACE_CALIBRATION_WORKLOAD_CONTRACT_V3"
FROZEN_BASE_SEED = 981301
FROZEN_UTILIZATIONS = ("1/5", "2/5")
FROZEN_TASKSETS_PER_UNIT = 50
FROZEN_STRUCTURAL_RETRY_LIMIT = 24
FROZEN_SCHEDULERS = (
    "gpfp_asap_block",
    "gpfp_alap_block",
    "gpfp_st_block",
)
FROZEN_PRIMARY_CANDIDATE = {
    "recovery_margin_ticks": 1,
    "interpolation_rho": "1/2",
    "nominal_energy_supply_ratio": "1/2",
}
FROZEN_MARGINS = (1, 3)
FROZEN_RHOS = ("1/3", "1/2")
FROZEN_ETAS = ("1/3", "1/2")
EXPECTED_CALIBRATION_UNITS = 18
EXPECTED_PAIRED_INSTANCES = 900
EXPECTED_SCHEDULER_REQUESTS = 2700
VALID_TERMINAL_STATUSES = {"SIM_PASS_OBSERVED", "SIM_DEADLINE_MISS"}

CALIBRATION_CLOSURE_FIELDS = (
    "identity_shape_audit_closed",
    "taskset_hash_audit_closed",
    "scenario_candidate_identity_audit_closed",
    "paired_instance_identity_audit_closed",
    "request_hash_audit_closed",
    "taskset_store_manifest_audit_closed",
    "output_file_hash_verification_closed",
    "hash_audit_closed",
    "pairing_audit_closed",
    "workload_audit_closed",
    "source_index_audit_closed",
    "calibration_unit_audit_closed",
)
PRIMARY_GATE_CHECK_KEYS = (
    "target_observation_denominator_exact",
    "target_initial_job_wait_100_percent",
    "target_positive_slack_transition_at_least_95_percent",
    "full_release_prefix_affordability_100_percent",
    "runtime_prefix_audit_closed_100_percent",
    "target_audit_closed_100_percent",
    "target_audit_errors_zero",
    "prefix_audit_errors_zero",
    "later_target_substitution_zero",
    "non_target_substitution_zero",
    "transition_after_slack_exhaustion_zero",
    "termination_without_transition_zero",
    "accepted_capacity_infeasible_tasks_zero",
    "accepted_capacity_infeasible_tasksets_zero",
    "all_hash_pairing_workload_source_output_audits_closed",
)


class CalibrationAuditError(RuntimeError):
    """The supplied calibration evidence is unreadable or malformed."""


def _canonical_fraction(value: Any, label: str) -> str:
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise CalibrationAuditError(f"{label} is not an exact fraction") from exc
    return str(parsed.numerator) if parsed.denominator == 1 else (
        f"{parsed.numerator}/{parsed.denominator}"
    )


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise CalibrationAuditError(f"{label} must be an integer")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise CalibrationAuditError(f"{label} must be an integer") from exc


def _boolean(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "1"}:
        return True
    if normalized in {"FALSE", "0"}:
        return False
    raise CalibrationAuditError(f"{label} must be boolean")


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise CalibrationAuditError(f"required calibration table is missing: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CalibrationAuditError(f"CSV has no header: {path}")
            return list(reader)
    except OSError as exc:
        raise CalibrationAuditError(f"cannot read calibration table: {path}") from exc


def _json_object(path: Path) -> dict[str, Any]:
    def no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CalibrationAuditError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationAuditError(f"cannot read JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise CalibrationAuditError(f"JSON evidence must be an object: {path}")
    return value


def _resolved_project_path(value: Any) -> Path:
    path = Path(str(value))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _require_fields(
    rows: Sequence[Mapping[str, Any]], fields: Iterable[str], label: str,
) -> None:
    required = set(fields)
    for index, row in enumerate(rows):
        missing = sorted(required.difference(row))
        if missing:
            raise CalibrationAuditError(
                f"{label}[{index}] is missing fields: {missing}"
            )


def _candidate_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        _integer(
            row.get("configured_recovery_margin_ticks"),
            "configured_recovery_margin_ticks",
        ),
        _canonical_fraction(row.get("interpolation_rho"), "interpolation_rho"),
        _canonical_fraction(
            row.get("nominal_energy_supply_ratio"),
            "nominal_energy_supply_ratio",
        ),
    )


def _candidate_document(key: tuple[int, str, str]) -> dict[str, Any]:
    return {
        "recovery_margin_ticks": key[0],
        "interpolation_rho": key[1],
        "nominal_energy_supply_ratio": key[2],
    }


def _ratio_document(numerator: int, denominator: int) -> dict[str, Any]:
    ratio = Fraction(numerator, denominator) if denominator else None
    return {
        "count": numerator,
        "denominator": denominator,
        "ratio": (
            None
            if ratio is None
            else str(ratio.numerator)
            if ratio.denominator == 1
            else f"{ratio.numerator}/{ratio.denominator}"
        ),
    }


def summarize_metric_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate exactly the frozen gate metrics without outcome selection."""

    def total(field: str) -> int:
        return sum(_integer(row.get(field), field) for row in rows)

    denominator = total("target_observation_denominator")
    wait = total("target_wait_observed_count")
    positive = total("target_positive_slack_transition_count")
    prefix_affordable = total("full_release_prefix_affordable_count")
    prefix_closed = total("recovery_prefix_audit_closed_count")
    target_closed = total("target_audit_closed_count")
    closures = {
        field: all(_boolean(row.get(field), field) for row in rows)
        for field in CALIBRATION_CLOSURE_FIELDS
    }
    return {
        "target_observation_denominator": denominator,
        "target_initial_job_wait": _ratio_document(wait, denominator),
        "target_positive_slack_transition": _ratio_document(
            positive, denominator,
        ),
        "full_release_prefix_affordability": _ratio_document(
            prefix_affordable, denominator,
        ),
        "runtime_prefix_audit_closed": _ratio_document(
            prefix_closed, denominator,
        ),
        "target_audit_closed": _ratio_document(target_closed, denominator),
        "target_audit_error_count": total("target_audit_error_count"),
        "prefix_audit_error_count": total("recovery_prefix_audit_error_count"),
        "later_target_job_positive_transition_count": total(
            "later_target_job_positive_transition_count"
        ),
        "later_target_substitution_count": total(
            "later_target_substitution_count"
        ),
        "non_target_positive_transition_count": total(
            "non_target_positive_transition_count"
        ),
        "non_target_substitution_count": total(
            "non_target_substitution_count"
        ),
        "activation_from_other_job_only_count": total(
            "activation_from_other_job_only_count"
        ),
        "transition_after_slack_exhaustion_count": total(
            "target_transition_after_slack_exhaustion_count"
        ),
        "termination_without_transition_count": total(
            "target_terminated_without_transition_count"
        ),
        "accepted_capacity_infeasible_task_count": total(
            "accepted_capacity_infeasible_task_count"
        ),
        "accepted_capacity_infeasible_taskset_count": total(
            "accepted_capacity_infeasible_taskset_count"
        ),
        "structurally_accepted_count": total("structurally_accepted_count"),
        "structural_rejection_attempt_count": total(
            "structural_rejection_attempt_count"
        ),
        "audit_closures": closures,
        "all_audit_closures": all(closures.values()),
    }


def evaluate_primary_numeric_gate(
    rows: Sequence[Mapping[str, Any]], *, expected_denominator: int,
) -> dict[str, Any]:
    """Evaluate the precommitted gate; it never compares candidates."""

    metrics = summarize_metric_rows(rows)
    denominator = int(metrics["target_observation_denominator"])
    checks = {
        "target_observation_denominator_exact": denominator
        == expected_denominator,
        "target_initial_job_wait_100_percent": (
            metrics["target_initial_job_wait"]["count"] == denominator
            and denominator == expected_denominator
        ),
        "target_positive_slack_transition_at_least_95_percent": (
            denominator > 0
            and Fraction(
                metrics["target_positive_slack_transition"]["count"],
                denominator,
            ) >= Fraction(19, 20)
        ),
        "full_release_prefix_affordability_100_percent": (
            metrics["full_release_prefix_affordability"]["count"]
            == denominator
            and denominator == expected_denominator
        ),
        "runtime_prefix_audit_closed_100_percent": (
            metrics["runtime_prefix_audit_closed"]["count"] == denominator
            and denominator == expected_denominator
        ),
        "target_audit_closed_100_percent": (
            metrics["target_audit_closed"]["count"] == denominator
            and denominator == expected_denominator
        ),
        "target_audit_errors_zero": metrics["target_audit_error_count"] == 0,
        "prefix_audit_errors_zero": metrics["prefix_audit_error_count"] == 0,
        "later_target_substitution_zero": (
            metrics["later_target_substitution_count"] == 0
        ),
        "non_target_substitution_zero": (
            metrics["non_target_substitution_count"] == 0
        ),
        "transition_after_slack_exhaustion_zero": (
            metrics["transition_after_slack_exhaustion_count"] == 0
        ),
        "termination_without_transition_zero": (
            metrics["termination_without_transition_count"] == 0
        ),
        "accepted_capacity_infeasible_tasks_zero": (
            metrics["accepted_capacity_infeasible_task_count"] == 0
        ),
        "accepted_capacity_infeasible_tasksets_zero": (
            metrics["accepted_capacity_infeasible_taskset_count"] == 0
        ),
        "all_hash_pairing_workload_source_output_audits_closed": metrics[
            "all_audit_closures"
        ] is True,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "metrics": metrics,
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
    require(config.get("parameter_status"), "CALIBRATION", "parameter_status")
    require(config.get("seed_space"), FROZEN_SEED_SPACE, "seed_space")
    require(tuple(config.get("scheduler_ids", ())), FROZEN_SCHEDULERS, "schedulers")
    grid = config.get("grid", {})
    require(tuple(grid.get("utilization_points", ())), FROZEN_UTILIZATIONS, "utilizations")
    require(grid.get("tasksets_per_cell"), FROZEN_TASKSETS_PER_UNIT, "tasksets_per_cell")
    require(grid.get("base_seed"), FROZEN_BASE_SEED, "base_seed")
    require(grid.get("seed_mode"), "generation_dimensions", "seed_mode")
    require(grid.get("taskset_index_start"), 0, "taskset_index_start")
    execution = config.get("execution", {})
    require(execution.get("resume"), False, "execution.resume")
    require(
        _resolved_project_path(execution.get("output_root")),
        output_root.resolve(),
        "execution.output_root",
    )
    configured_store = _resolved_project_path(execution.get("taskset_store"))
    if configured_store == output_root.resolve():
        errors.append("taskset_store must be distinct from output_root")
    scenario = config.get("scenario", {})
    require(
        scenario.get("structural_retry_limit"),
        FROZEN_STRUCTURAL_RETRY_LIMIT,
        "structural_retry_limit",
    )
    calibration_grid = scenario.get("calibration_grid", {})
    require(
        tuple(calibration_grid.get("recovery_margin_ticks", ())),
        FROZEN_MARGINS,
        "recovery margins",
    )
    require(
        tuple(calibration_grid.get("interpolation_rhos", ())),
        FROZEN_RHOS,
        "interpolation rhos",
    )
    require(
        tuple(calibration_grid.get("nominal_energy_supply_ratios", ())),
        FROZEN_ETAS,
        "nominal supply ratios",
    )
    timing_cells = scenario.get("timing_cells", ())
    require(len(timing_cells), 2, "timing cell declarations")
    if len(timing_cells) == 2:
        require(
            tuple(row.get("subtype") for row in timing_cells),
            ("POSITIVE_SLACK_ENERGY_AVAILABLE", "SLACK_LIMITED_CHARGING"),
            "timing cell subtypes",
        )
    try:
        description = Ext1BRunner(config).describe()
    except Exception as exc:
        errors.append(f"runner description failed: {exc}")
        description = {}
    for field, expected in (
        ("cell_count", EXPECTED_CALIBRATION_UNITS),
        ("paired_instance_count", EXPECTED_PAIRED_INSTANCES),
        ("simulation_request_count", EXPECTED_SCHEDULER_REQUESTS),
    ):
        require(description.get(field), expected, f"runner {field}")
    return errors, {
        "source_config": str(config_path.resolve()),
        "source_config_hash": ext1b_config_hash(config),
        "configured_output_root": str(
            _resolved_project_path(execution.get("output_root"))
        ),
        "configured_taskset_store": str(configured_store),
        "runner_description": description,
    }


def audit_generation_attempt_history(
    attempts: Sequence[Mapping[str, Any]],
    generated: Sequence[Mapping[str, Any]],
    instances: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
    *,
    scenario_cell_ids: Sequence[str],
    utilizations: Sequence[str],
    logical_index_start: int,
    tasksets_per_unit: int,
    retry_limit: int,
    scheduler_ids: Sequence[str],
) -> tuple[list[str], dict[str, Any]]:
    """Audit complete structural-retry histories and accepted identities."""

    def identity_text(value: Any) -> bool:
        return isinstance(value, str) and bool(value)

    errors: list[str] = []
    groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    row_identity_closed = True
    for row_number, row in enumerate(attempts, start=1):
        try:
            key = (
                str(row["scenario_cell_id"]),
                _canonical_fraction(
                    row["normalized_utilization"],
                    f"generation_attempts[{row_number}] utilization",
                ),
                _integer(
                    row["logical_taskset_index"],
                    f"generation_attempts[{row_number}] logical index",
                ),
            )
        except (KeyError, CalibrationAuditError) as exc:
            errors.append(f"invalid generation attempt group identity: {exc}")
            row_identity_closed = False
            continue
        groups[key].append(row)

    expected_units = {
        (str(cell_id), _canonical_fraction(utilization, "expected utilization"))
        for cell_id in scenario_cell_ids
        for utilization in utilizations
    }
    expected_logical_indices = set(range(
        logical_index_start,
        logical_index_start + tasksets_per_unit,
    ))
    expected_groups = {
        (cell_id, utilization, logical_index)
        for cell_id, utilization in expected_units
        for logical_index in expected_logical_indices
    }
    observed_groups = set(groups)
    logical_index_domain_closed = (
        row_identity_closed and observed_groups == expected_groups
    )
    if observed_groups != expected_groups:
        missing = sorted(expected_groups - observed_groups)
        extra = sorted(observed_groups - expected_groups)
        if missing:
            errors.append(
                f"missing generation-attempt logical groups: {missing[:5]}"
            )
        if extra:
            errors.append(
                f"unexpected generation-attempt logical groups: {extra[:5]}"
            )

    missing_attempt_index_count = 0
    duplicate_attempt_index_count = 0
    accepted_not_last_count = 0
    multiple_accepted_count = 0
    no_accepted_count = 0
    complete_group_count = 0
    accepted_by_group: dict[
        tuple[str, str, int], Mapping[str, Any]
    ] = {}
    all_group_keys = expected_groups | observed_groups
    for key in sorted(all_group_keys):
        members = groups.get(key, [])
        group_closed = key in expected_groups and bool(members)
        parsed: list[tuple[int, Mapping[str, Any]]] = []
        for row in members:
            try:
                logical = _integer(
                    row.get("logical_taskset_index"), "logical_taskset_index"
                )
                compatibility_logical = _integer(
                    row.get("logical_index"), "logical_index"
                )
                attempt_index = _integer(
                    row.get("attempt_index"), "attempt_index"
                )
                source_index = _integer(
                    row.get("source_taskset_index"), "source_taskset_index"
                )
                compatibility_source = _integer(
                    row.get("source_index"), "source_index"
                )
            except CalibrationAuditError as exc:
                errors.append(f"invalid attempt row for {key}: {exc}")
                group_closed = False
                continue
            if logical != key[2] or compatibility_logical != logical:
                errors.append(f"logical-index compatibility mismatch for {key}")
                group_closed = False
            if not 0 <= attempt_index < retry_limit:
                errors.append(f"attempt index outside retry limit for {key}")
                group_closed = False
            expected_source = logical * retry_limit + attempt_index
            if source_index != expected_source or compatibility_source != source_index:
                errors.append(f"source-index identity mismatch for {key}")
                group_closed = False
            parsed.append((attempt_index, row))

        index_counts = Counter(index for index, _row in parsed)
        duplicate_count = sum(count - 1 for count in index_counts.values())
        duplicate_attempt_index_count += duplicate_count
        if duplicate_count:
            errors.append(f"duplicate attempt index for {key}")
            group_closed = False
        accepted = [
            (index, row) for index, row in parsed
            if str(row.get("attempt_status")) == "ACCEPTED"
        ]
        unknown_status = any(
            str(row.get("attempt_status")) not in {"ACCEPTED", "REJECTED"}
            for _index, row in parsed
        )
        if unknown_status:
            errors.append(f"unknown attempt status for {key}")
            group_closed = False
        if not accepted:
            no_accepted_count += 1
            group_closed = False
        elif len(accepted) > 1:
            multiple_accepted_count += 1
            errors.append(f"multiple accepted attempts for {key}")
            group_closed = False
        else:
            accepted_index, accepted_row = accepted[0]
            accepted_by_group[key] = accepted_row
            observed_unique = set(index_counts)
            required = set(range(accepted_index + 1)) if accepted_index >= 0 else set()
            missing_count = len(required - observed_unique)
            missing_attempt_index_count += missing_count
            if missing_count:
                errors.append(f"attempt sequence has a gap for {key}")
                group_closed = False
            maximum_index = max(observed_unique, default=-1)
            if accepted_index != maximum_index:
                accepted_not_last_count += 1
                errors.append(f"accepted attempt is not last for {key}")
                group_closed = False
            if observed_unique != required:
                group_closed = False
            for attempt_index, row in sorted(parsed, key=lambda item: item[0]):
                expected_status = (
                    "ACCEPTED" if attempt_index == accepted_index else "REJECTED"
                )
                if str(row.get("attempt_status")) != expected_status:
                    group_closed = False
                    errors.append(f"attempt status sequence mismatch for {key}")
                    break
        if group_closed:
            complete_group_count += 1

    incomplete_group_count = (
        len(expected_groups) - complete_group_count
        + len(observed_groups - expected_groups)
    )
    attempt_sequence_audit_closed = all((
        logical_index_domain_closed,
        complete_group_count == len(expected_groups),
        missing_attempt_index_count == 0,
        duplicate_attempt_index_count == 0,
        accepted_not_last_count == 0,
        multiple_accepted_count == 0,
        no_accepted_count == 0,
    ))

    cross_errors_before = len(errors)
    request_ids = [row.get("request_id") for row in requests]
    valid_request_ids = [
        request_id for request_id in request_ids
        if identity_text(request_id)
    ]
    if (
        len(valid_request_ids) != len(request_ids)
        or len(valid_request_ids) != len(set(valid_request_ids))
    ):
        errors.append("simulation request IDs are empty or duplicated")
    generated_used: set[int] = set()
    instances_used: set[int] = set()
    requests_used: set[int] = set()
    for key in sorted(expected_groups):
        accepted = accepted_by_group.get(key)
        if accepted is None:
            continue
        cell_id, utilization, logical_index = key
        source_taskset_id_value = accepted.get("source_taskset_id")
        source_taskset_id = (
            source_taskset_id_value
            if identity_text(source_taskset_id_value) else ""
        )
        matching_generated = [
            (index, row) for index, row in enumerate(generated)
            if str(row.get("scenario_cell_id")) == cell_id
            and str(row.get("source_taskset_id")) == source_taskset_id
            and str(row.get("logical_taskset_index")) == str(logical_index)
        ]
        if len(matching_generated) != 1:
            errors.append(f"accepted attempt/generated row mismatch for {key}")
            continue
        generated_index, generated_row = matching_generated[0]
        generated_used.add(generated_index)
        try:
            accepted_attempt_index = _integer(
                accepted.get("attempt_index"), "accepted attempt index"
            )
            accepted_source_index = _integer(
                accepted.get("source_taskset_index"), "accepted source index"
            )
            generated_attempt_index = _integer(
                generated_row.get("accepted_attempt_index"),
                "generated accepted attempt index",
            )
        except CalibrationAuditError as exc:
            errors.append(f"invalid accepted/generated identity for {key}: {exc}")
            continue
        generated_identity_closed = all((
            identity_text(source_taskset_id),
            identity_text(accepted.get("source_taskset_hash")),
            identity_text(accepted.get("generation_seed")),
            generated_attempt_index == accepted_attempt_index,
            accepted_source_index
            == logical_index * retry_limit + generated_attempt_index,
            identity_text(generated_row.get("source_taskset_id")),
            identity_text(generated_row.get("source_taskset_hash")),
            identity_text(generated_row.get("generation_seed")),
            str(generated_row.get("source_taskset_hash"))
            == str(accepted.get("source_taskset_hash")),
            str(generated_row.get("generation_seed"))
            == str(accepted.get("generation_seed")),
            identity_text(generated_row.get("taskset_id")),
            identity_text(generated_row.get("taskset_hash")),
        ))
        if not generated_identity_closed:
            errors.append(f"accepted/generated identity mismatch for {key}")
            continue

        paired_instance_id_value = accepted.get("paired_instance_id")
        paired_instance_id = (
            paired_instance_id_value
            if identity_text(paired_instance_id_value) else ""
        )
        matching_instances = [
            (index, row) for index, row in enumerate(instances)
            if str(row.get("paired_instance_id")) == paired_instance_id
        ]
        if not paired_instance_id or len(matching_instances) != 1:
            errors.append(f"accepted/scenario instance mismatch for {key}")
            continue
        instance_index, instance_row = matching_instances[0]
        instances_used.add(instance_index)
        try:
            instance_utilization = _canonical_fraction(
                instance_row.get("normalized_utilization"),
                "scenario instance utilization",
            )
            instance_logical = _integer(
                instance_row.get("logical_taskset_index"),
                "scenario instance logical index",
            )
        except CalibrationAuditError as exc:
            errors.append(f"invalid scenario instance identity for {key}: {exc}")
            continue
        instance_identity_closed = all((
            identity_text(instance_row.get("paired_instance_id")),
            identity_text(instance_row.get("scenario_cell_id")),
            identity_text(instance_row.get("taskset_id")),
            identity_text(instance_row.get("taskset_hash")),
            identity_text(instance_row.get("generation_seed")),
            str(instance_row.get("scenario_cell_id")) == cell_id,
            instance_utilization == utilization,
            instance_logical == logical_index,
            str(instance_row.get("taskset_id"))
            == str(generated_row.get("taskset_id")),
            str(instance_row.get("taskset_hash"))
            == str(generated_row.get("taskset_hash")),
            str(instance_row.get("generation_seed"))
            == str(accepted.get("generation_seed")),
        ))
        if not instance_identity_closed:
            errors.append(f"generated/scenario instance identity mismatch for {key}")
            continue

        matching_requests = [
            (index, row) for index, row in enumerate(requests)
            if str(row.get("paired_instance_id")) == paired_instance_id
        ]
        request_schedulers = tuple(
            str(row.get("scheduler_id")) for _index, row in matching_requests
        )
        request_identity_closed = (
            len(matching_requests) == len(scheduler_ids)
            and request_schedulers == tuple(scheduler_ids)
            and all(
                identity_text(row.get("request_id"))
                and identity_text(row.get("paired_instance_id"))
                and identity_text(row.get("scenario_cell_id"))
                and identity_text(row.get("taskset_id"))
                and identity_text(row.get("taskset_hash"))
                and identity_text(row.get("generation_seed"))
                and str(row.get("scenario_cell_id")) == cell_id
                and str(row.get("taskset_id"))
                == str(generated_row.get("taskset_id"))
                and str(row.get("taskset_hash"))
                == str(generated_row.get("taskset_hash"))
                and str(row.get("generation_seed"))
                == str(accepted.get("generation_seed"))
                for _index, row in matching_requests
            )
            and not any(
                str(row.get("attempt_status")) == "REJECTED"
                and str(row.get("paired_instance_id", "")) == paired_instance_id
                for row in groups.get(key, ())
            )
        )
        if not request_identity_closed:
            errors.append(f"accepted/request identity mismatch for {key}")
            continue
        requests_used.update(index for index, _row in matching_requests)

    if len(generated_used) != len(generated):
        errors.append("generated taskset rows do not bijectively match accepted attempts")
    if len(instances_used) != len(instances):
        errors.append("scenario instance rows do not bijectively match accepted attempts")
    if len(requests_used) != len(requests):
        errors.append("simulation request rows do not bijectively match accepted attempts")
    accepted_cross_table_identity_audit_closed = (
        attempt_sequence_audit_closed
        and len(errors) == cross_errors_before
        and len(generated_used) == len(generated) == len(expected_groups)
        and len(instances_used) == len(instances) == len(expected_groups)
        and len(requests_used)
        == len(requests) == len(expected_groups) * len(scheduler_ids)
    )
    return errors, {
        "logical_attempt_group_count": len(observed_groups),
        "complete_attempt_history_group_count": complete_group_count,
        "incomplete_attempt_history_group_count": incomplete_group_count,
        "missing_attempt_index_count": missing_attempt_index_count,
        "duplicate_attempt_index_count": duplicate_attempt_index_count,
        "accepted_not_last_count": accepted_not_last_count,
        "multiple_accepted_count": multiple_accepted_count,
        "no_accepted_count": no_accepted_count,
        "logical_index_domain_closed": logical_index_domain_closed,
        "attempt_sequence_audit_closed": attempt_sequence_audit_closed,
        "accepted_cross_table_identity_audit_closed": (
            accepted_cross_table_identity_audit_closed
        ),
    }


def _dataset_integrity_audit(
    config: Mapping[str, Any], output_root: Path,
) -> tuple[list[str], dict[str, Any], list[dict[str, str]]]:
    errors: list[str] = []
    persisted = load_ext1b_config(output_root / "run_config.yaml")
    if ext1b_config_hash(persisted) != ext1b_config_hash(config):
        errors.append("persisted run_config does not match the frozen source config")
    checkpoint = _json_object(output_root / "checkpoint.json")
    if checkpoint.get("requested") != EXPECTED_SCHEDULER_REQUESTS:
        errors.append("checkpoint requested count is not 2700")
    if checkpoint.get("terminal") != EXPECTED_SCHEDULER_REQUESTS:
        errors.append("checkpoint terminal count is not 2700")
    if checkpoint.get("pending") != 0:
        errors.append("checkpoint has pending requests")
    if checkpoint.get("stop_requested") is not False:
        errors.append("checkpoint records a stop request")

    metadata = _json_object(output_root / "run_metadata.json")
    if metadata.get("experiment_id") != FROZEN_EXPERIMENT_ID:
        errors.append("run_metadata experiment_id mismatch")
    if metadata.get("parameter_status") != "CALIBRATION":
        errors.append("run_metadata parameter_status is not CALIBRATION")
    if metadata.get("seed_space") != FROZEN_SEED_SPACE:
        errors.append("run_metadata seed_space mismatch")
    if metadata.get("config_hash") != ext1b_config_hash(config):
        errors.append("run_metadata config_hash mismatch")
    if metadata.get("selection_policy") != (
        "structural predicates and runtime activation only; scheduler outcomes excluded"
    ):
        errors.append("run_metadata selection policy is not outcome-blind")

    generated = _csv_rows(output_root / "generated_tasksets.csv")
    attempts = _csv_rows(output_root / "generation_attempts.csv")
    instances = _csv_rows(output_root / "scenario_instances.csv")
    requests = _csv_rows(output_root / "simulation_requests.csv")
    simulation_attempts = _csv_rows(output_root / "simulation_attempts.csv")
    results = _csv_rows(output_root / "simulation_results.csv")
    failures = _csv_rows(output_root / "failures.csv")
    b3_rows = _csv_rows(output_root / "b3_summary.csv")
    calibration_rows = _csv_rows(output_root / "b3_calibration_summary.csv")

    expected_lengths = (
        ("generated tasksets", generated, EXPECTED_PAIRED_INSTANCES),
        ("scenario instances", instances, EXPECTED_PAIRED_INSTANCES),
        ("simulation requests", requests, EXPECTED_SCHEDULER_REQUESTS),
        ("simulation attempts", simulation_attempts, EXPECTED_SCHEDULER_REQUESTS),
        ("simulation results", results, EXPECTED_SCHEDULER_REQUESTS),
        ("B3 summary", b3_rows, EXPECTED_SCHEDULER_REQUESTS),
        ("B3 calibration summary", calibration_rows, EXPECTED_CALIBRATION_UNITS),
    )
    for label, rows, expected in expected_lengths:
        if len(rows) != expected:
            errors.append(f"{label} row count is {len(rows)}, expected {expected}")
    if failures:
        errors.append(f"runner failure table is non-empty ({len(failures)} rows)")

    request_ids = [str(row.get("request_id")) for row in requests]
    result_ids = [str(row.get("request_id")) for row in results]
    attempt_ids = [str(row.get("request_id")) for row in simulation_attempts]
    if len(set(request_ids)) != len(request_ids):
        errors.append("simulation_requests contains duplicate request IDs")
    if set(result_ids) != set(request_ids) or len(result_ids) != len(set(result_ids)):
        errors.append("simulation_results do not form a unique complete request set")
    if set(attempt_ids) != set(request_ids) or len(attempt_ids) != len(set(attempt_ids)):
        errors.append("simulation_attempts do not form a unique complete request set")
    try:
        assert_ext1b_fair_pairing(requests, FROZEN_SCHEDULERS)
    except RuntimeError as exc:
        errors.append(f"paired request audit failed: {exc}")
    if any(str(row.get("request_status")) != "PLANNED" for row in requests):
        errors.append("simulation_requests contains a non-PLANNED request")

    terminal_statuses = Counter(str(row.get("status")) for row in results)
    invalid_statuses = sorted(set(terminal_statuses) - VALID_TERMINAL_STATUSES)
    if invalid_statuses:
        errors.append(f"invalid calibration terminal statuses: {invalid_statuses}")
    attempt_statuses = Counter(str(row.get("status")) for row in simulation_attempts)
    invalid_attempt_statuses = sorted(set(attempt_statuses) - VALID_TERMINAL_STATUSES)
    if invalid_attempt_statuses:
        errors.append(
            f"runner failures/timeouts in simulation attempts: {invalid_attempt_statuses}"
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
        errors.append(f"unclassifiable timing transitions observed: {unclassifiable_count}")
    if b3_audit_errors:
        errors.append(f"B3 timing audit errors observed: {b3_audit_errors}")
    if b3_open_count:
        errors.append(f"B3 timing audits not closed: {b3_open_count}")

    accepted_attempts = [
        row for row in attempts if str(row.get("attempt_status")) == "ACCEPTED"
    ]
    scenario_cell_ids = sorted({
        str(cell[1]["scenario_cell_id"])
        for cell in Ext1BRunner(config).describe()["cells"]
    })
    history_errors, history_report = audit_generation_attempt_history(
        attempts,
        generated,
        instances,
        requests,
        scenario_cell_ids=scenario_cell_ids,
        utilizations=FROZEN_UTILIZATIONS,
        logical_index_start=int(config["grid"]["taskset_index_start"]),
        tasksets_per_unit=FROZEN_TASKSETS_PER_UNIT,
        retry_limit=FROZEN_STRUCTURAL_RETRY_LIMIT,
        scheduler_ids=FROZEN_SCHEDULERS,
    )
    errors.extend(history_errors)
    if len(accepted_attempts) != EXPECTED_PAIRED_INSTANCES:
        errors.append("accepted generation attempt count is not 900")
    if len({row.get("taskset_hash") for row in generated}) != len(generated):
        errors.append("generated taskset hashes are not unique")

    file_hashes_valid = verify_file_hashes(output_root)
    if not file_hashes_valid:
        errors.append("output file hash verification failed")
    return errors, {
        "checkpoint": checkpoint,
        "terminal_status_counts": dict(sorted(terminal_statuses.items())),
        "simulation_attempt_status_counts": dict(sorted(attempt_statuses.items())),
        "runner_failure_count": len(failures),
        "illegal_timing_transition_count": illegal_count,
        "unclassifiable_timing_transition_count": unclassifiable_count,
        "b3_timing_audit_error_count": b3_audit_errors,
        "b3_timing_open_audit_count": b3_open_count,
        "generated_taskset_count": len(generated),
        "generation_attempt_count": len(attempts),
        "accepted_generation_attempt_count": len(accepted_attempts),
        **history_report,
        "paired_instance_count": len(instances),
        "scheduler_request_count": len(requests),
        "terminal_result_count": len(results),
        "calibration_unit_count": len(calibration_rows),
        "file_hashes_valid": file_hashes_valid,
        "sim_deadline_miss_is_schedulability_or_performance_evidence": False,
    }, calibration_rows


def _calibration_grid_audit(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[str], dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    errors: list[str] = []
    _require_fields(
        rows,
        (
            "normalized_utilization", "timing_subtype",
            "configured_recovery_margin_ticks", "interpolation_rho",
            "nominal_energy_supply_ratio",
            "target_recovery_contract_applicable",
            "target_observation_denominator", "target_wait_observed_count",
            "target_positive_slack_transition_count",
            "full_release_prefix_affordable_count",
            "recovery_prefix_audit_closed_count", "target_audit_closed_count",
            "target_audit_error_count", "recovery_prefix_audit_error_count",
            "later_target_job_positive_transition_count",
            "later_target_substitution_count",
            "non_target_positive_transition_count",
            "non_target_substitution_count",
            "activation_from_other_job_only_count",
            "target_transition_after_slack_exhaustion_count",
            "target_terminated_without_transition_count",
            "accepted_capacity_infeasible_task_count",
            "accepted_capacity_infeasible_taskset_count",
            "structurally_accepted_count", "structural_rejection_attempt_count",
            *CALIBRATION_CLOSURE_FIELDS,
        ),
        "b3_calibration_summary",
    )
    charging_by_candidate: dict[
        tuple[int, str, str], list[dict[str, str]]
    ] = defaultdict(list)
    controls: list[dict[str, str]] = []
    expected_candidates = {
        (margin, rho, eta)
        for margin in FROZEN_MARGINS
        for rho in FROZEN_RHOS
        for eta in FROZEN_ETAS
    }
    unit_counts: Counter[tuple[str, str]] = Counter()
    observed_by_util: dict[str, set[tuple[int, str, str]]] = defaultdict(set)
    for row in rows:
        utilization = _canonical_fraction(
            row.get("normalized_utilization"), "calibration utilization"
        )
        subtype = str(row.get("timing_subtype"))
        if utilization not in FROZEN_UTILIZATIONS:
            errors.append(f"unexpected calibration utilization: {utilization}")
        unit_counts[(utilization, subtype)] += 1
        if _integer(row.get("structurally_accepted_count"), "accepted count") != (
            FROZEN_TASKSETS_PER_UNIT
        ):
            errors.append(
                f"calibration unit {row.get('scenario_cell_id')} does not "
                "contain 50 accepted tasksets"
            )
        if any(
            not _boolean(row.get(field), field)
            for field in CALIBRATION_CLOSURE_FIELDS
        ):
            errors.append(
                f"calibration unit {row.get('scenario_cell_id')} has an open audit"
            )
        if _integer(
            row.get("accepted_capacity_infeasible_task_count"),
            "capacity-infeasible task count",
        ) != 0 or _integer(
            row.get("accepted_capacity_infeasible_taskset_count"),
            "capacity-infeasible taskset count",
        ) != 0:
            errors.append(
                f"calibration unit {row.get('scenario_cell_id')} accepted "
                "capacity-infeasible evidence"
            )
        if subtype == "POSITIVE_SLACK_ENERGY_AVAILABLE":
            controls.append(dict(row))
            if _boolean(
                row.get("target_recovery_contract_applicable"),
                "positive-control recovery applicability",
            ):
                errors.append("positive control is incorrectly recovery-applicable")
            if _integer(
                row.get("target_observation_denominator"),
                "positive-control target denominator",
            ) != 0:
                errors.append("positive control unexpectedly enters the charging gate")
            continue
        if subtype != "SLACK_LIMITED_CHARGING":
            errors.append(f"unknown calibration timing subtype: {subtype}")
            continue
        if not _boolean(
            row.get("target_recovery_contract_applicable"),
            "charging recovery applicability",
        ):
            errors.append("charging calibration unit is not recovery-applicable")
        key = _candidate_key(row)
        charging_by_candidate[key].append(dict(row))
        observed_by_util[utilization].add(key)

    for utilization in FROZEN_UTILIZATIONS:
        if unit_counts[(utilization, "POSITIVE_SLACK_ENERGY_AVAILABLE")] != 1:
            errors.append(f"utilization {utilization} does not have exactly one positive control")
        if unit_counts[(utilization, "SLACK_LIMITED_CHARGING")] != 8:
            errors.append(f"utilization {utilization} does not have eight charging units")
        if observed_by_util[utilization] != expected_candidates:
            errors.append(f"utilization {utilization} charging grid is incomplete or changed")
    if set(charging_by_candidate) != expected_candidates:
        errors.append("the eight-candidate charging grid is incomplete or changed")
    if any(len(candidate_rows) != 2 for candidate_rows in charging_by_candidate.values()):
        errors.append("a charging candidate does not have one row per utilization")
    return errors, charging_by_candidate, controls


def audit_calibration(config_path: Path, output_root: Path) -> dict[str, Any]:
    """Return a complete read-only acceptance report."""

    config = load_ext1b_config(config_path)
    config_errors, config_report = _frozen_config_audit(
        config, config_path, output_root,
    )
    dataset_errors, dataset_report, calibration_rows = _dataset_integrity_audit(
        config, output_root,
    )
    grid_errors, charging, controls = _calibration_grid_audit(calibration_rows)
    integrity_errors = config_errors + dataset_errors + grid_errors
    integrity_passed = not integrity_errors

    primary_key = (
        FROZEN_PRIMARY_CANDIDATE["recovery_margin_ticks"],
        FROZEN_PRIMARY_CANDIDATE["interpolation_rho"],
        FROZEN_PRIMARY_CANDIDATE["nominal_energy_supply_ratio"],
    )
    primary_rows = charging.get(primary_key, [])
    primary_per_utilization: dict[str, Any] = {}
    for utilization in FROZEN_UTILIZATIONS:
        unit_rows = [
            row for row in primary_rows
            if _canonical_fraction(
                row.get("normalized_utilization"), "primary utilization"
            ) == utilization
        ]
        primary_per_utilization[utilization] = evaluate_primary_numeric_gate(
            unit_rows,
            expected_denominator=FROZEN_TASKSETS_PER_UNIT,
        )
    primary_overall = evaluate_primary_numeric_gate(
        primary_rows,
        expected_denominator=(
            len(FROZEN_UTILIZATIONS) * FROZEN_TASKSETS_PER_UNIT
        ),
    )
    primary_numeric_passed = (
        all(row["passed"] for row in primary_per_utilization.values())
        and primary_overall["passed"]
    )

    candidate_reports = []
    for key in sorted(charging, key=lambda item: (item != primary_key, item)):
        candidate_rows = charging[key]
        per_utilization = {}
        for utilization in FROZEN_UTILIZATIONS:
            unit_rows = [
                row for row in candidate_rows
                if _canonical_fraction(
                    row.get("normalized_utilization"), "candidate utilization"
                ) == utilization
            ]
            per_utilization[utilization] = summarize_metric_rows(unit_rows)
        candidate_reports.append({
            "parameters": _candidate_document(key),
            "role": "PRECOMMITTED_PRIMARY" if key == primary_key else "DIAGNOSTIC_ONLY",
            "formal_selection_eligible": key == primary_key,
            "per_utilization": per_utilization,
            "overall": summarize_metric_rows(candidate_rows),
        })

    primary_passed = integrity_passed and primary_numeric_passed
    return {
        "schema": REPORT_SCHEMA,
        "protocol_state": "CALIBRATION_ONLY",
        "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
        "automatic_parameter_replacement_permitted": False,
        "alternate_candidates_are_diagnostic_only": True,
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
                controls,
                key=lambda item: Fraction(str(item["normalized_utilization"])),
            )
        ],
        "charging_candidates": candidate_reports,
        "primary_gate": {
            "passed": primary_passed,
            "dataset_integrity_passed": integrity_passed,
            "numeric_gate_passed": primary_numeric_passed,
            "per_utilization": primary_per_utilization,
            "overall": primary_overall,
        },
        "calibration_passed": primary_passed,
        "formal_profile_created_or_authorized": False,
        "sim_deadline_miss_use": "TERMINAL_STATUS_ONLY_NOT_SCHEDULABILITY_OR_PERFORMANCE_EVIDENCE",
        "on_primary_failure": "RETAIN_EVIDENCE_AND_REDESIGN_BY_NEW_PR_AND_NEW_PROTOCOL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            PROJECT_ROOT
            / "configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml"
        ),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit_calibration(args.config, args.output_root.resolve())
    except Exception as exc:
        report = {
            "schema": REPORT_SCHEMA,
            "protocol_state": "CALIBRATION_ONLY",
            "frozen_primary_candidate": dict(FROZEN_PRIMARY_CANDIDATE),
            "automatic_parameter_replacement_permitted": False,
            "dataset_integrity": {"passed": False, "errors": [str(exc)]},
            "primary_gate": {"passed": False},
            "calibration_passed": False,
            "formal_profile_created_or_authorized": False,
            "on_primary_failure": (
                "RETAIN_EVIDENCE_AND_REDESIGN_BY_NEW_PR_AND_NEW_PROTOCOL"
            ),
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report.get("calibration_passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
