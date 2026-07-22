"""Frozen B3-v2 FORMAL profile and fail-closed evidence tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
import yaml

from experiments.v9_3.ext1b_config import (
    FORMAL_PROFILE_BY_SEED_SPACE,
    ext1b_config_hash,
    load_ext1b_config,
    validate_ext1b_config,
)
from experiments.v9_3.ext1b_engine import Ext1BRunner
from scripts.audit_v9_3_ext1b3_b3_v2_calibration import (
    CALIBRATION_CLOSURE_FIELDS,
)
from scripts.audit_v9_3_ext1b3_b3_v2_formal_confirmation import (
    BASE_SEED_LABEL,
    BOOTSTRAP_SEED_LABEL,
    EXPECTED_PAIRED_INSTANCES,
    EXPECTED_SCHEDULER_REQUESTS,
    FROZEN_BASE_SEED,
    FROZEN_BOOTSTRAP_SEED,
    FROZEN_EXPERIMENT_ID,
    FROZEN_PARAMETERS,
    FROZEN_SCHEDULERS,
    FROZEN_SEED_SPACE,
    FROZEN_SIMULATOR_SHA256,
    PROJECT_ROOT,
    _formal_units_audit,
    _frozen_config_audit,
    audit_dataset_tables,
    audit_formal_confirmation,
)


CONFIG_PATH = (
    PROJECT_ROOT / "configs/v9_3_ext1b3_b3_v2_formal_confirmation_r1.yaml"
)
CALIBRATION_PATH = (
    PROJECT_ROOT
    / "configs/v9_3_ext1b3_timing_calibration_v2_target_trace_contract.yaml"
)


def _derived_seed(label: str) -> int:
    digest = hashlib.sha256(label.encode("ascii")).digest()
    return 1 + int.from_bytes(digest[:8], "big") % (2**31 - 1)


def _metric_row(
    *, utilization: str, subtype: str, transitions: int | None = None,
) -> dict[str, object]:
    charging = subtype == "SLACK_LIMITED_CHARGING"
    denominator = 200 if charging else 0
    transition_count = denominator if transitions is None else transitions
    row: dict[str, object] = {
        "scenario_cell_id": (
            "slack-limited-charging-v2-formal-r1-margin-00-rho-00-eta-00"
            if charging
            else "positive-slack-energy-available-v2-formal-r1"
        ),
        "normalized_utilization": utilization,
        "timing_subtype": subtype,
        "configured_recovery_margin_ticks": 1 if charging else 0,
        "interpolation_rho": "1/2",
        "nominal_energy_supply_ratio": "1/2" if charging else "0",
        "target_recovery_contract_applicable": charging,
        "target_observation_denominator": denominator,
        "target_wait_observed_count": denominator,
        "target_positive_slack_transition_count": transition_count,
        "full_release_prefix_affordable_count": denominator,
        "recovery_prefix_audit_closed_count": denominator,
        "target_audit_closed_count": denominator,
        "target_audit_error_count": 0,
        "recovery_prefix_audit_error_count": 0,
        "later_target_job_positive_transition_count": 0,
        "later_target_substitution_count": 0,
        "non_target_positive_transition_count": 0,
        "non_target_substitution_count": 0,
        "activation_from_other_job_only_count": 0,
        "target_transition_after_slack_exhaustion_count": 0,
        "target_terminated_without_transition_count": 0,
        "accepted_capacity_infeasible_task_count": 0,
        "accepted_capacity_infeasible_taskset_count": 0,
        "structurally_accepted_count": 200,
        "structural_rejection_attempt_count": 0,
    }
    row.update({field: True for field in CALIBRATION_CLOSURE_FIELDS})
    return row


def _valid_evidence(config: dict) -> dict[str, object]:
    generated: list[dict[str, object]] = []
    generation_attempts: list[dict[str, object]] = []
    instances: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []
    simulation_attempts: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    b3_rows: list[dict[str, object]] = []

    scenario_ids = (
        "positive-slack-energy-available-v2-formal-r1",
        "slack-limited-charging-v2-formal-r1-margin-00-rho-00-eta-00",
    )
    request_number = 0
    for utilization in ("1/5", "2/5"):
        for scenario_id in scenario_ids:
            for logical_index in range(200):
                cell_tag = scenario_id[:8]
                util_tag = utilization.replace("/", "-")
                identity = f"{util_tag}-{cell_tag}-{logical_index}"
                pair_id = f"pair-{identity}"
                source_index = logical_index * 24
                source_id = f"source-{util_tag}-{logical_index}"
                source_hash = f"source-hash-{util_tag}-{logical_index}"
                taskset_id = f"taskset-{identity}"
                taskset_hash = hashlib.sha256(taskset_id.encode()).hexdigest()
                seed = str(FROZEN_BASE_SEED + source_index)
                generation_attempts.append({
                    "scenario_cell_id": scenario_id,
                    "normalized_utilization": utilization,
                    "logical_taskset_index": logical_index,
                    "logical_index": logical_index,
                    "attempt_index": 0,
                    "source_taskset_index": source_index,
                    "source_index": source_index,
                    "generation_seed": seed,
                    "source_taskset_id": source_id,
                    "source_taskset_hash": source_hash,
                    "attempt_status": "ACCEPTED",
                    "paired_instance_id": pair_id,
                })
                generated.append({
                    "scenario_cell_id": scenario_id,
                    "logical_taskset_index": logical_index,
                    "accepted_attempt_index": 0,
                    "source_taskset_id": source_id,
                    "source_taskset_hash": source_hash,
                    "generation_seed": seed,
                    "taskset_id": taskset_id,
                    "taskset_hash": taskset_hash,
                })
                instances.append({
                    "paired_instance_id": pair_id,
                    "scenario_cell_id": scenario_id,
                    "normalized_utilization": utilization,
                    "logical_taskset_index": logical_index,
                    "taskset_id": taskset_id,
                    "taskset_hash": taskset_hash,
                    "generation_seed": seed,
                })
                fair = {
                    "paired_instance_id": pair_id,
                    "scenario_cell_id": scenario_id,
                    "taskset_id": taskset_id,
                    "taskset_hash": taskset_hash,
                    "trace_hash": f"trace-{identity}",
                    "simulation_config_hash": f"simulation-{identity}",
                    "input_hash": f"input-{identity}",
                    "initial_battery": "0",
                    "battery_capacity": "1",
                    "horizon": 400,
                    "maximum_horizon": 400,
                    "generation_seed": seed,
                    "M": 4,
                    "priority_hash": f"priority-{identity}",
                    "power_hash": f"power-{identity}",
                    "deadline_hash": f"deadline-{identity}",
                    "release_hash": f"release-{identity}",
                    "workload_vector_hash": f"workload-{identity}",
                    "simulator_build_hash": FROZEN_SIMULATOR_SHA256,
                    "scenario_contract_id": (
                        "B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2"
                    ),
                    "target_runtime_task_name": f"target-{identity}",
                    "target_arrival_time": 0,
                    "target_job_id": f"target-job-{identity}",
                    "target_recovery_contract_applicable": (
                        scenario_id.startswith("slack-limited")
                    ),
                    "recovery_prefix_identity": f"prefix-{identity}",
                    "recovery_prefix_length": 4,
                    "recovery_prefix_runtime_names_json": "[]",
                    "recovery_prefix_required_energy": "1",
                    "materialized_battery_capacity": "1",
                    "actual_trace_target_affordable_tick": 1,
                    "actual_trace_full_tick": 2,
                }
                for scheduler_id in FROZEN_SCHEDULERS:
                    request_id = f"request-{request_number}"
                    request_number += 1
                    requests.append({
                        **fair,
                        "request_id": request_id,
                        "scheduler_id": scheduler_id,
                        "request_status": "PLANNED",
                    })
                    simulation_attempts.append({
                        "request_id": request_id,
                        "status": "SIM_PASS_OBSERVED",
                    })
                    results.append({
                        "request_id": request_id,
                        "status": "SIM_PASS_OBSERVED",
                    })
                    b3_rows.append({
                        "request_id": request_id,
                        "audit_closed": True,
                        "audit_error_count": 0,
                        "timing_illegal_count": 0,
                        "timing_unclassifiable_count": 0,
                    })

    formal_rows = [
        _metric_row(utilization=utilization, subtype=subtype)
        for utilization in ("1/5", "2/5")
        for subtype in (
            "POSITIVE_SLACK_ENERGY_AVAILABLE",
            "SLACK_LIMITED_CHARGING",
        )
    ]
    request_ids = [str(row["request_id"]) for row in requests]
    config_hash = ext1b_config_hash(config)
    return {
        "checkpoint": {
            "schema": "ASAP_BLOCK_V9_3_EXT1B_CHECKPOINT_V1",
            "config_hash": config_hash,
            "requested": EXPECTED_SCHEDULER_REQUESTS,
            "terminal": EXPECTED_SCHEDULER_REQUESTS,
            "pending": 0,
            "stop_requested": False,
        },
        "metadata": {
            "experiment_id": FROZEN_EXPERIMENT_ID,
            "parameter_status": "FORMAL",
            "seed_space": FROZEN_SEED_SPACE,
            "config_hash": config_hash,
            "bootstrap_seed": FROZEN_BOOTSTRAP_SEED,
            "bootstrap_resamples": 2000,
            "selection_policy": (
                "structural predicates and runtime activation only; "
                "scheduler outcomes excluded"
            ),
            "git_head": "a" * 40,
            "simulator_path": str(PROJECT_ROOT / "build/rtsim/rtsim"),
            "simulator_build_hash": FROZEN_SIMULATOR_SHA256,
            "target_trace_contract": {
                "scenario_contract_id": (
                    "B3_TARGET_ACTUAL_TRACE_RECOVERY_CONTRACT_V2"
                ),
            },
        },
        "generated": generated,
        "generation_attempts": generation_attempts,
        "instances": instances,
        "requests": requests,
        "simulation_attempts": simulation_attempts,
        "results": results,
        "failures": [],
        "b3_rows": b3_rows,
        "formal_rows": formal_rows,
        "terminal_ids": request_ids,
        "file_hashes_valid": True,
    }


def test_formal_profile_freezes_independent_identity_scale_and_single_candidate():
    config = load_ext1b_config(CONFIG_PATH)
    calibration = load_ext1b_config(CALIBRATION_PATH)
    description = Ext1BRunner(config).describe()

    assert config["parameter_status"] == "FORMAL"
    assert config["experiment_id"] == FROZEN_EXPERIMENT_ID
    assert config["experiment_id"] != calibration["experiment_id"]
    assert config["seed_space"] == FROZEN_SEED_SPACE
    assert config["seed_space"] != calibration["seed_space"]
    assert config["grid"]["base_seed"] == FROZEN_BASE_SEED
    assert config["grid"]["base_seed"] != calibration["grid"]["base_seed"]
    assert config["statistics"]["bootstrap_seed"] == FROZEN_BOOTSTRAP_SEED
    assert config["statistics"]["bootstrap_seed"] != calibration["statistics"]["bootstrap_seed"]
    assert FROZEN_BASE_SEED == _derived_seed(BASE_SEED_LABEL)
    assert FROZEN_BOOTSTRAP_SEED == _derived_seed(BOOTSTRAP_SEED_LABEL)
    assert config["scenario"]["calibration_grid"] == {
        "recovery_margin_ticks": [1],
        "interpolation_rhos": ["1/2"],
        "nominal_energy_supply_ratios": ["1/2"],
    }
    assert description["cell_count"] == 4
    assert description["paired_instance_count"] == 800
    assert description["simulation_request_count"] == 2400
    assert config["grid"]["tasksets_per_cell"] == 200
    assert config["grid"]["utilization_points"] == ["1/5", "2/5"]
    assert len(config["scenario"]["timing_cells"]) == 2
    assert len(config["scheduler_ids"]) == 3
    assert config["execution"]["resume"] is False
    assert config["execution"]["output_root"] != config["execution"]["taskset_store"]
    assert "calibration" not in config["execution"]["output_root"]
    assert "calibration" not in config["execution"]["taskset_store"]
    assert FORMAL_PROFILE_BY_SEED_SPACE[FROZEN_SEED_SPACE]["profile_id"] == "B3_V2"


def test_formal_config_rejects_each_diagnostic_candidate_and_identity_mutation():
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    mutations = (
        ("recovery_margin_ticks", [1, 3]),
        ("interpolation_rhos", ["1/3", "1/2"]),
        ("nominal_energy_supply_ratios", ["1/3", "1/2"]),
    )
    for field, value in mutations:
        changed = deepcopy(raw)
        changed["scenario"]["calibration_grid"][field] = value
        with pytest.raises(Exception, match="FORMAL profile B3_V2"):
            validate_ext1b_config(changed)


def test_formal_config_auditor_freezes_all_identity_fields():
    config = load_ext1b_config(CONFIG_PATH)
    output_root = PROJECT_ROOT / config["execution"]["output_root"]
    errors, report = _frozen_config_audit(config, CONFIG_PATH, output_root)
    assert errors == []
    assert report["runner_description"]["cell_count"] == 4
    changed = deepcopy(config)
    changed["execution"]["resume"] = True
    errors, _ = _frozen_config_audit(changed, CONFIG_PATH, output_root)
    assert any("execution.resume" in error for error in errors)


def test_complete_synthetic_formal_dataset_closes():
    config = load_ext1b_config(CONFIG_PATH)
    errors, report = audit_dataset_tables(config, **_valid_evidence(config))
    assert errors == []
    assert report["paired_instance_count"] == EXPECTED_PAIRED_INSTANCES
    assert report["scheduler_request_count"] == EXPECTED_SCHEDULER_REQUESTS
    assert report["file_hashes_valid"] is True


@pytest.mark.parametrize(
    "mutation,expected_error",
    (
        ("bad_hash", "output file hash verification failed"),
        ("wrong_request_count", "simulation requests row count"),
        ("pending", "checkpoint pending mismatch"),
        ("invalid_status", "invalid formal terminal statuses"),
        ("duplicate_request", "duplicate request IDs"),
        ("incomplete_pair", "paired request audit failed"),
        ("missing_terminal", "terminal results does not form"),
    ),
)
def test_formal_dataset_audit_rejects_integrity_failures(
    mutation: str, expected_error: str,
):
    config = load_ext1b_config(CONFIG_PATH)
    evidence = _valid_evidence(config)
    if mutation == "bad_hash":
        evidence["file_hashes_valid"] = False
    elif mutation == "wrong_request_count":
        evidence["requests"] = list(evidence["requests"][:-1])
    elif mutation == "pending":
        evidence["checkpoint"]["pending"] = 1
    elif mutation == "invalid_status":
        evidence["results"][0]["status"] = "SIM_RUNTIME_TIMEOUT"
    elif mutation == "duplicate_request":
        evidence["requests"][-1]["request_id"] = evidence["requests"][0]["request_id"]
    elif mutation == "incomplete_pair":
        evidence["requests"] = list(evidence["requests"][:-1])
    elif mutation == "missing_terminal":
        evidence["terminal_ids"] = list(evidence["terminal_ids"][:-1])

    errors, _ = audit_dataset_tables(config, **evidence)
    assert any(expected_error in error for error in errors)


def test_formal_units_reject_non_frozen_candidate_without_replacement():
    rows = [
        _metric_row(utilization=utilization, subtype=subtype)
        for utilization in ("1/5", "2/5")
        for subtype in (
            "POSITIVE_SLACK_ENERGY_AVAILABLE",
            "SLACK_LIMITED_CHARGING",
        )
    ]
    rows[1]["configured_recovery_margin_ticks"] = 3
    errors, charging, _controls = _formal_units_audit(rows)
    assert charging
    assert any("non-frozen candidate" in error for error in errors)
    assert FROZEN_PARAMETERS == {
        "recovery_margin_ticks": 1,
        "interpolation_rho": "1/2",
        "nominal_energy_supply_ratio": "1/2",
    }


def test_missing_evidence_fails_closed_and_does_not_write(tmp_path: Path):
    before = list(tmp_path.rglob("*"))
    report = audit_formal_confirmation(CONFIG_PATH, tmp_path)
    after = list(tmp_path.rglob("*"))
    assert report["decision"] == "FORMAL_CONFIRMATION_FAILED"
    assert report["formal_confirmation_passed"] is False
    assert report["parameter_selection_permitted"] is False
    assert report["parameters_may_be_adjusted_from_this_result"] is False
    assert before == after == []
