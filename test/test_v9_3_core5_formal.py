from __future__ import annotations

from collections import Counter
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from experiments.v9_3.config import config_hash, dump_config, load_config
from experiments.v9_3.core5_contract import (
    Core5ContractError, validate_core5_artifact_contract,
    write_core5_hash_manifest,
)
from experiments.v9_3.core5_formal import (
    CORE5A_METRICS_SCHEMA,
    CORE5B_WORKER_CHECK_COLUMNS,
    CORE5_FORMAL_CHECKPOINT_SCHEMA,
    CORE5_FORMAL_RUN_SCHEMA,
    Core5FormalContractError,
    Core5FormalRunner,
    FormalChildEvidence,
    analyze_core5_formal_artifacts,
    assert_worker_semantic_identity,
    build_worker_semantic_checks,
    core5b_execution_schedule,
    core5b_math_request_rows,
    exact_time_scale_payload,
    expand_core5a_cells,
    inspect_formal_child,
)
from experiments.v9_3.resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS, FAILURE_COLUMNS, GENERATED_COLUMNS, REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS, TASK_RESULT_COLUMNS,
    atomic_write_json, read_csv, write_csv,
)


ROOT = Path(__file__).resolve().parents[1]
CORE5A = ROOT / "configs/v9_3_core5a_formal_algorithmic.yaml"
CORE5B = ROOT / "configs/v9_3_core5b_formal_workers.yaml"
SMOKE = ROOT / "configs/v9_3_core5_smoke.yaml"


def _reduced_formal_config(source: Path, tmp_path: Path):
    config = deepcopy(load_config(source, expected_core="CORE-5"))
    config["grid"]["utilization_points"] = [
        config["grid"]["utilization_points"][0]
    ]
    config["scalability"]["utilization_points"] = [
        config["scalability"]["utilization_points"][0]
    ]
    config["grid"]["tasksets_per_cell"] = 1
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    return config


def _write_child_artifacts(
    child, request_ids, *, complete_count: int,
):
    root = Path(child["execution"]["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    identity = config_hash(child)
    authorization_id = "test-formal-authorization"
    atomic_write_json(root / "run_metadata.json", {
        "schema": "ASAP_BLOCK_V9_3_FORMAL_RUN_V1",
        "experiment_id": child["experiment_id"],
        "core": "CORE-5",
        "config_hash": identity,
        "formal_large_scale_run": True,
        "formal_authorization_id": authorization_id,
    })
    atomic_write_json(root / "formal_authorization_seal.json", {
        "formal_large_scale_run": True,
        "authorization_id": authorization_id,
    })
    dump_config(child, root / "run_config.yaml")

    requests = []
    generated = []
    attempts = []
    results = []
    tasks = []
    observations = []
    completed_ids = []
    for index, analysis_id in enumerate(request_ids):
        request_id = f"request-{analysis_id}"
        taskset_id = f"taskset-{index}"
        taskset_hash = f"taskset-hash-{index}"
        variant = child["analysis"]["variants"][
            index % len(child["analysis"]["variants"])
        ]
        completed = index < complete_count
        requests.append({
            "request_id": request_id,
            "analysis_id": analysis_id,
            "cell_id": f"cell-{index}",
            "taskset_id": taskset_id,
            "taskset_hash": taskset_hash,
            "exact_e0": child["energy"]["initial_energy_values"][0],
            "variant": variant,
            "numerical_mode": child["analysis"]["numerical_mode"],
            "timeout_seconds": child["analysis"]["timeout_seconds"],
            "retry_timeout_seconds": child["analysis"].get(
                "retry_timeout_seconds"
            ),
            "source_analysis_id": "",
            "request_status": "TERMINAL" if completed else "PLANNED",
        })
        generated.append({
            "generation_id": f"generation-{index}",
            "taskset_id": taskset_id,
            "taskset_index": index,
            "generation_seed": index,
            "M": child["platform"]["cores"][0],
            "task_n": child["platform"]["task_count"][0],
            "target_total_utilization": child["grid"][
                "utilization_points"
            ][0],
            "actual_total_utilization": child["grid"][
                "utilization_points"
            ][0],
            "utilization_error_total": "0",
            "deadline_mode": child["generation"]["deadline_mode"],
            "d_over_t_min_actual": "1/2",
            "d_over_t_max_actual": "1/2",
            "d_over_t_values_json": "[\"1/2\"]",
            "taskset_hash": taskset_hash,
            "priority_hash": "priority",
            "power_hash": "power",
            "service_curve_reference": "service",
            "generation_seconds": "0.001",
            "canonical_taskset_json": "{}",
            "task_input_json": "{}",
        })
        if not completed:
            continue
        completed_ids.append(analysis_id)
        attempt_id = f"attempt-{analysis_id}"
        attempts.append({
            "attempt_id": attempt_id,
            "analysis_id": analysis_id,
            "attempt_number": 1,
            "parent_attempt_id": "",
            "timeout_budget_seconds": child["analysis"]["timeout_seconds"],
            "solver_status": "COMPLETED",
            "failure_origin": "",
            "outer_timeout": False,
            "solver_wall_seconds": "0.100000000",
            "solver_cpu_seconds": "0.080000000",
            "worker_startup_seconds": "0.010000000",
            "serialization_seconds": "0.001000000",
            "ipc_seconds": "0.002000000",
            "payload_received": True,
            "worker_cleanup_status": "CLEAN",
            "worker_exitcode": 0,
            "worker_cleanup_seconds": "0.001000000",
            "total_wall_seconds": "0.120000000",
            "exception_type": "",
            "exception_message": "",
            "traceback": "",
            "started_at_utc": "TEST",
        })
        results.append({
            "analysis_id": analysis_id,
            "request_id": request_id,
            "cell_id": f"cell-{index}",
            "taskset_id": taskset_id,
            "taskset_hash": taskset_hash,
            "generation_seed": index,
            "M": child["platform"]["cores"][0],
            "task_n": child["platform"]["task_count"][0],
            "utilization": child["grid"]["utilization_points"][0],
            "exact_e0": child["energy"]["initial_energy_values"][0],
            "deadline_mode": child["generation"]["deadline_mode"],
            "analysis_variant": variant,
            "method_role": "PRIMARY",
            "solver_status": "COMPLETED",
            "failure_origin": "",
            "certification_status": "CERTIFIED",
            "taskset_proven": True,
            "first_failed_priority": "",
            "n_tasks_total": 1,
            "n_tasks_evaluated": 1,
            "n_tasks_candidate_found": 1,
            "n_tasks_certified": 1,
            "source_analysis_id": "",
            "source_vector_hash": "",
            "target_carry_in_vector_hash": "",
            "dependency_check_status": "NOT_APPLICABLE",
            "fixed_carry_in_interface_status": "VALID",
            "dominance_invariant_status": "VALID",
            "dominance_violation_count": 0,
            "diagnostic_mode": False,
            "final_attempt_id": attempt_id,
            "attempt_count": 1,
            "timeout_budget_seconds": child["analysis"]["timeout_seconds"],
            "runtime_wall_seconds": "0.100000000",
            "runtime_cpu_seconds": "0.080000000",
            "worker_startup_seconds": "0.010000000",
            "serialization_seconds": "0.001000000",
            "ipc_seconds": "0.002000000",
            "outer_timeout": False,
            "terminal_origin": "SOLVER",
        })
        tasks.append({
            "analysis_id": analysis_id,
            "cell_id": f"cell-{index}",
            "taskset_id": taskset_id,
            "exact_e0": child["energy"]["initial_energy_values"][0],
            "analysis_variant": variant,
            "task_id": 0,
            "priority_rank": 0,
            "C": 1,
            "D": 2,
            "T": 4,
            "P": "1",
            "D_over_T": "1/2",
            "task_solver_status": "COMPLETED",
            "task_certification_status": "CERTIFIED",
            "candidate_response_time": 2,
            "closing_w": 2,
            "witness_h": 1,
            "checked_w_count": 3,
            "checked_h_count": 4,
            "checked_q_count": 5,
            "envelope_call_count": 6,
            "failure_reason": "",
            "carry_in_vector_hash": "carry",
        })
        observations.append({
            "attempt_id": attempt_id,
            "analysis_id": analysis_id,
            "peak_rss_kib": 1024 + index,
            "peak_rss_scope": "CHILD_PROCESS",
            "peak_rss_unit": "KiB",
            "observation_status": "AVAILABLE",
            "unavailability_reason": "",
        })
    write_csv(root / "analysis_requests.csv", REQUEST_COLUMNS, requests)
    write_csv(root / "generated_tasksets.csv", GENERATED_COLUMNS, generated)
    write_csv(root / "analysis_attempts.csv", ATTEMPT_COLUMNS, attempts)
    write_csv(
        root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results
    )
    write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, tasks)
    write_csv(
        root / "attempt_resource_observations.csv",
        RESOURCE_OBSERVATION_COLUMNS, observations,
    )
    write_csv(root / "failures.csv", FAILURE_COLUMNS, [])
    atomic_write_json(root / "checkpoint.json", {
        "config_hash": identity,
        "completed_analysis_ids": completed_ids,
        "completed_count": len(completed_ids),
        "requested_count": len(request_ids),
        "stop_requested": complete_count != len(request_ids),
    })


def test_core5a_formal_plan_and_duplicate_baseline_elimination():
    config = load_config(CORE5A, expected_core="CORE-5")
    cells = expand_core5a_cells(config)
    assert len(cells) == 24
    assert Counter(cell.utilization for cell in cells) == {
        Fraction(3, 10): 8, Fraction(1, 2): 8, Fraction(7, 10): 8,
    }
    for utilization in {cell.utilization for cell in cells}:
        members = [cell for cell in cells if cell.utilization == utilization]
        mathematical_inputs = {
            tuple(cell.mathematical_input().values()) for cell in members
        }
        assert len(mathematical_inputs) == 8
        assert sum(
            cell.processors == 4 and cell.task_count == 10
            and cell.period_min == 40 and cell.period_max == 200
            for cell in members
        ) == 1
        assert sum(
            cell.processors == 4 and cell.task_count == 20
            and cell.period_min == 40 and cell.period_max == 200
            for cell in members
        ) == 1
    plan = Core5FormalRunner(config).describe()
    assert plan["cell_count"] == 24
    assert plan["unique_scale_configurations_per_utilization"] == 8
    assert plan["mathematical_request_count"] == 4800
    assert plan["solver_execution_count"] == 4800


def test_core5a_exact_time_scaling_preserves_ratios_and_power():
    source = (
        {
            "task_id": "0", "priority_rank": 0, "C": 3, "D": 7, "T": 11,
            "P": "3/5", "D_over_T": "7/11", "workload": "hash",
        },
        {
            "task_id": "1", "priority_rank": 1, "C": 2, "D": 5, "T": 13,
            "P": "1/2", "D_over_T": "5/13", "workload": "control",
        },
    )
    for factor in (Fraction(1), Fraction(2), Fraction(4)):
        scaled = exact_time_scale_payload(source, factor)
        for original, transformed in zip(source, scaled):
            assert transformed["C"] == original["C"] * factor
            assert transformed["D"] == original["D"] * factor
            assert transformed["T"] == original["T"] * factor
            assert Fraction(transformed["C"], transformed["T"]) == Fraction(
                original["C"], original["T"]
            )
            assert Fraction(transformed["D_over_T"]) == Fraction(
                original["D_over_T"]
            )
            assert transformed["P"] == original["P"]
            assert transformed["task_id"] == original["task_id"]
            assert transformed["source_task_id"] == original["task_id"]


def test_core5b_math_identity_and_seeded_schedule_contract():
    config = load_config(CORE5B, expected_core="CORE-5")
    requests = core5b_math_request_rows(config)
    assert len(requests) == 300
    assert len({row["mathematical_request_id"] for row in requests}) == 300
    assert len({row["input_hash"] for row in requests}) == 300
    schedule = core5b_execution_schedule(config)
    assert schedule == core5b_execution_schedule(config)
    assert len(schedule) == 20
    assert {
        (row["worker_count"], row["repetition"]) for row in schedule
    } == {
        (worker, repetition) for worker in (1, 2, 4, 8)
        for repetition in range(5)
    }
    assert list(schedule) != sorted(
        schedule, key=lambda row: (row["worker_count"], row["repetition"])
    )
    plan = Core5FormalRunner(config).describe()
    assert plan["mathematical_request_count"] == 300
    assert plan["input_hash_count"] == 300
    assert plan["solver_execution_count"] == 6000


def _semantic_row(worker: int, response: str = "[10]"):
    return {
        "mathematical_request_id": "request",
        "worker_count": worker,
        "input_hash": "input",
        "terminal_class": "COMPLETED",
        "response_bound": response,
        "fixed_point_iterations": "[4]",
        "search_states": "[[5,6]]",
        "inverse_service_queries": "[7]",
        "candidate_count": 1,
    }


def test_core5b_worker_semantic_identity_is_p0_on_any_mismatch():
    assert_worker_semantic_identity([_semantic_row(1), _semantic_row(8)])
    with pytest.raises(Core5FormalContractError, match="P0 worker semantic mismatch"):
        assert_worker_semantic_identity([
            _semantic_row(1), _semantic_row(8, response="[11]"),
        ])


def test_invalid_child_hash_fails_closed_and_missing_child_is_fresh(tmp_path):
    config = _reduced_formal_config(CORE5B, tmp_path)
    runner = Core5FormalRunner(config)
    child = runner._child_config(
        run_id="w1-r0", processors=4, task_count=10,
        period_min=40, period_max=200,
        utilizations=config["scalability"]["utilization_points"],
        tasksets=1, worker_count=1,
    )
    assert inspect_formal_child(
        child, expected_request_count=2
    ).state == "FRESH"
    _write_child_artifacts(
        child, ["math-0", "math-1"], complete_count=1
    )
    metadata_path = Path(child["execution"]["output_root"]) / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["config_hash"] = "0" * 64
    atomic_write_json(metadata_path, metadata)
    with pytest.raises(Core5FormalContractError, match="hash mismatch"):
        inspect_formal_child(child, expected_request_count=2)


def test_core5a_parent_resume_continues_real_partial_child_artifact(
    tmp_path, monkeypatch
):
    import experiments.v9_3.core5_formal as formal_module

    config = _reduced_formal_config(CORE5A, tmp_path)
    cell = expand_core5a_cells(config)[0]
    monkeypatch.setattr(
        formal_module, "expand_core5a_cells", lambda _config: (cell,)
    )
    monkeypatch.setattr(
        formal_module, "prepare_service_curve",
        lambda _config, _root: object(),
    )
    runner = Core5FormalRunner(config)
    runner._initialize(resume=False)
    child = runner._child_config(
        run_id=cell.cell_id, processors=cell.processors,
        task_count=cell.task_count, period_min=cell.period_min,
        period_max=cell.period_max,
        utilizations=[str(cell.utilization)], tasksets=1,
        worker_count=1,
    )
    request_ids = ["core5a-resume-0", "core5a-resume-1"]
    _write_child_artifacts(child, request_ids, complete_count=1)
    resume_values = []

    def finish_child(observed_child, *, resume, service=None, store=None):
        resume_values.append(resume)
        _write_child_artifacts(
            observed_child, request_ids, complete_count=len(request_ids)
        )
        return (
            FormalChildEvidence(
                "COMPLETED", 2, 2, {"COMPLETED": 2}
            ),
            {
                "wall_seconds": 1.0, "cpu_seconds": 0.8,
                "peak_rss_kib": 1025, "analyses_per_second": 2.0,
            },
        )

    monkeypatch.setattr(runner, "_run_child", finish_child)
    summary = runner.run(resume=True)
    assert resume_values == [True]
    assert summary["solver_execution_count"] == 2
    assert inspect_formal_child(
        child, expected_request_count=2
    ).state == "COMPLETED"


def test_core5b_parent_resume_continues_real_partial_child_artifact(
    tmp_path, monkeypatch
):
    config = _reduced_formal_config(CORE5B, tmp_path)
    config["scalability"]["worker_counts"] = [1]
    config["scalability"]["repetitions_per_worker"] = 1
    runner = Core5FormalRunner(config)
    runner._initialize(resume=False)
    scheduled = core5b_execution_schedule(config)[0]
    child = runner._child_config(
        run_id="w1-r0", processors=4, task_count=10,
        period_min=40, period_max=200,
        utilizations=config["scalability"]["utilization_points"],
        tasksets=1, worker_count=1,
    )
    request_ids = ["core5b-resume-0", "core5b-resume-1"]
    _write_child_artifacts(child, request_ids, complete_count=1)
    resume_values = []

    def finish_child(observed_child, *, resume, service=None, store=None):
        resume_values.append(resume)
        _write_child_artifacts(
            observed_child, request_ids, complete_count=len(request_ids)
        )
        return (
            FormalChildEvidence(
                "COMPLETED", 2, 2, {"COMPLETED": 2}
            ),
            {
                "wall_seconds": 1.0, "cpu_seconds": 0.8,
                "peak_rss_kib": 1025, "analyses_per_second": 2.0,
            },
        )

    monkeypatch.setattr(runner, "_run_child", finish_child)
    summary = runner.run(resume=True)
    assert scheduled == {"run_order": 0, "worker_count": 1, "repetition": 0}
    assert resume_values == [True]
    assert summary["solver_execution_count"] == 2
    checks = read_csv(runner.root / "worker_semantic_checks.csv")
    assert len(checks) == 2
    assert all(row["execution_count"] == "1" for row in checks)


def test_core5a_analyzer_reconstructs_persisted_child_metrics(tmp_path):
    config = _reduced_formal_config(CORE5A, tmp_path)
    runner = Core5FormalRunner(config)
    runner._initialize(resume=False)
    for cell in expand_core5a_cells(config):
        child = runner._child_config(
            run_id=cell.cell_id, processors=cell.processors,
            task_count=cell.task_count, period_min=cell.period_min,
            period_max=cell.period_max,
            utilizations=[str(cell.utilization)], tasksets=1,
            worker_count=1,
        )
        _write_child_artifacts(
            child,
            [f"{cell.cell_id}-0", f"{cell.cell_id}-1"],
            complete_count=2,
        )
    summary = runner.run(resume=True)
    metrics = json.loads(
        (runner.root / "core5a_metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["schema"] == CORE5A_METRICS_SCHEMA
    assert metrics["overall"]["execution_count"] == 16
    assert metrics["overall"]["runtime_seconds"]["median"] == 0.1
    assert metrics["overall"]["runtime_seconds"]["p95"] == 0.1
    assert metrics["overall"]["peak_rss_kib"]["max"] == 1025
    assert metrics["overall"]["fixed_point_iterations"] == {
        "observation_status": "UNAVAILABLE",
        "available_observation_count": 0,
        "total": None,
    }
    assert metrics["overall"]["terminal_status_counts"] == {
        "COMPLETED": 16
    }
    assert metrics["overall"]["search_counters"] == {
        "checked_w_count": {
            "observation_status": "AVAILABLE",
            "available_observation_count": 16,
            "total": 48,
        },
        "checked_h_count": {
            "observation_status": "AVAILABLE",
            "available_observation_count": 16,
            "total": 64,
        },
        "checked_q_count": {
            "observation_status": "AVAILABLE",
            "available_observation_count": 16,
            "total": 80,
        },
    }
    assert metrics["overall"]["inverse_service_queries"]["total"] == 96
    assert metrics["overall"]["candidate_counts"]["total"] == 16
    assert metrics["overall"]["timeout_retry_counts"] == {
        "terminal_timeout_count": 0,
        "attempt_timeout_count": 0,
        "analysis_retry_count": 0,
        "retry_attempt_count": 0,
        "total_attempt_count": 16,
    }
    assert metrics["overall"]["censoring_state_counts"] == {
        "SCIENTIFIC_COMPLETION": 16,
        "RIGHT_CENSORED_TIMEOUT": 0,
        "TECHNICAL_FAILURE": 0,
    }
    assert summary["solver_execution_count"] == 16

    metrics["overall"]["fixed_point_iterations"]["total"] = 0
    atomic_write_json(runner.root / "core5a_metrics.json", metrics)
    write_core5_hash_manifest(runner.root)
    with pytest.raises(
        Core5FormalContractError, match="persisted metric mismatch"
    ):
        analyze_core5_formal_artifacts(runner.root)


def test_core5b_analyzer_reconstructs_twenty_execution_semantics(tmp_path):
    config = _reduced_formal_config(CORE5B, tmp_path)
    runner = Core5FormalRunner(config)
    runner._initialize(resume=False)
    request_ids = ["math-0", "math-1"]
    for scheduled in core5b_execution_schedule(config):
        run_id = f"w{scheduled['worker_count']}-r{scheduled['repetition']}"
        child = runner._child_config(
            run_id=run_id, processors=4, task_count=10,
            period_min=40, period_max=200,
            utilizations=config["scalability"]["utilization_points"],
            tasksets=1, worker_count=scheduled["worker_count"],
        )
        _write_child_artifacts(child, request_ids, complete_count=2)
    summary = runner.run(resume=True)
    checks = read_csv(runner.root / "worker_semantic_checks.csv")
    assert len(checks) == 2
    assert all(row["execution_count"] == "20" for row in checks)
    assert all(
        json.loads(row["worker_execution_counts_json"])
        == {"1": 5, "2": 5, "4": 5, "8": 5}
        for row in checks
    )
    assert all(row["fixed_point_iterations"] == "UNAVAILABLE" for row in checks)
    assert summary["solver_execution_count"] == 40

    checks[0]["candidate_count"] = "0"
    write_csv(
        runner.root / "worker_semantic_checks.csv",
        CORE5B_WORKER_CHECK_COLUMNS, checks,
    )
    write_core5_hash_manifest(runner.root)
    with pytest.raises(
        Core5FormalContractError, match="persisted worker semantic checks"
    ):
        analyze_core5_formal_artifacts(runner.root)


def test_formal_smoke_config_identity_output_store_and_seal_are_isolated():
    smoke = load_config(SMOKE, expected_core="CORE-5")
    core5a = load_config(CORE5A, expected_core="CORE-5")
    core5b = load_config(CORE5B, expected_core="CORE-5")
    configs = [smoke, core5a, core5b]
    assert len({config_hash(config) for config in configs}) == 3
    assert len({config["execution"]["output_root"] for config in configs}) == 3
    assert len({config["execution"]["taskset_store"] for config in configs}) == 3
    assert "profile" not in smoke["scalability"]
    assert core5a["scalability"]["profile"] == "formal-algorithmic-v1"
    assert core5b["scalability"]["profile"] == "formal-workers-v1"


def test_formal_and_bounded_analyzers_reject_mixed_profiles(tmp_path):
    (tmp_path / "run_metadata.json").write_text(json.dumps({
        "schema": CORE5_FORMAL_RUN_SCHEMA,
    }), encoding="utf-8")
    (tmp_path / "checkpoint.json").write_text(json.dumps({
        "schema": CORE5_FORMAL_CHECKPOINT_SCHEMA,
    }), encoding="utf-8")
    with pytest.raises(Core5ContractError, match="run metadata schema"):
        validate_core5_artifact_contract(tmp_path)

    shutil.copy2(SMOKE, tmp_path / "run_config.yaml")
    (tmp_path / "formal_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "formal_authorization_seal.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(Core5FormalContractError, match="bounded profile"):
        analyze_core5_formal_artifacts(tmp_path)


def test_smoke_v2_8_16_20_dry_run_contract_is_unchanged():
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_v9_3_core5.py", "--config",
            "configs/v9_3_core5_smoke.yaml", "--dry-run",
        ],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert '"cell_count": 8' in completed.stdout
    assert '"request_count": 16' in completed.stdout
    assert '"hard_analysis_limit": 20' in completed.stdout
    assert '"profile"' not in completed.stdout
