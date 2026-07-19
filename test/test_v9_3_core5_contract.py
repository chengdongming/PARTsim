from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.v9_3.config import canonical_json, domain_hash, load_config
from experiments.v9_3.core5_contract import (
    CORE5_CHECKPOINT_SCHEMA,
    CORE5_RUN_SCHEMA,
    Core5ContractError,
    validate_core5_artifact_contract,
    validate_core5_hash_manifest,
    write_core5_hash_manifest,
)
from experiments.v9_3 import core5_scalability as scalability_module
from experiments.v9_3.core5_scalability import Core5ScalabilityRunner
from experiments.v9_3.execution_engine import RunOutcome
from experiments.v9_3.resource_measurement import RESOURCE_OBSERVATION_COLUMNS
from experiments.v9_3.result_writer import (
    ATTEMPT_COLUMNS,
    FAILURE_COLUMNS,
    GENERATED_COLUMNS,
    REQUEST_COLUMNS,
    TASKSET_RESULT_COLUMNS,
    TASK_RESULT_COLUMNS,
    read_csv,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path) -> dict:
    config = load_config(
        ROOT / "configs/v9_3_core5_smoke.yaml", expected_core="CORE-5"
    )
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    return config


def _install_fake_pipeline(monkeypatch, *, mode: str = "success"):
    class FakeEngine:
        calls: list[dict] = []

        def __init__(self, child, **kwargs):
            self.child = child

        def run(self, **kwargs):
            call = len(self.calls)
            self.calls.append(deepcopy(self.child))
            root = Path(self.child["execution"]["output_root"])
            variants = list(self.child["analysis"]["variants"])
            math_preimage = {
                "M": self.child["platform"]["cores"][0],
                "task_n": self.child["platform"]["task_count"][0],
                "period_min": self.child["generation"]["period_min"],
                "period_max": self.child["generation"]["period_max"],
                "utilization": self.child["grid"]["utilization_points"][0],
                "seed": self.child["grid"]["base_seed"],
            }
            generation_id = domain_hash("FAKE:CORE5:GENERATION", math_preimage)
            taskset_hash = domain_hash("FAKE:CORE5:TASKSET", math_preimage)
            if mode == "worker_hash_mismatch" and call == 7:
                taskset_hash = domain_hash(
                    "FAKE:CORE5:TASKSET", {**math_preimage, "bad_worker": 2}
                )
            taskset_id = f"fake-{taskset_hash[:20]}"
            tasks_input = [{
                "task_id": "0", "priority_rank": 0, "C": 1,
                "D": 2, "T": 4, "P": "1",
            }]
            priority_hash = domain_hash(
                "FAKE:PRIORITY", [{"task_id": "0", "priority_rank": 0}]
            )
            power_hash = domain_hash(
                "FAKE:POWER", [{"task_id": "0", "P": "1"}]
            )
            generated = [{
                "generation_id": generation_id, "taskset_id": taskset_id,
                "taskset_index": 0, "generation_seed": 930435,
                "M": math_preimage["M"], "task_n": math_preimage["task_n"],
                "target_total_utilization": "1/5",
                "actual_total_utilization": "1/5",
                "utilization_error_total": "0", "deadline_mode": "constrained",
                "d_over_t_min_actual": "1/2", "d_over_t_max_actual": "1/2",
                "d_over_t_values_json": canonical_json(["1/2"]),
                "taskset_hash": taskset_hash, "priority_hash": priority_hash,
                "power_hash": power_hash,
                "service_curve_reference": domain_hash(
                    "FAKE:SERVICE", {
                        "declaration": self.child["energy"]["service_curve"],
                        "period_max": math_preimage["period_max"],
                    },
                ),
                "generation_seconds": "0.000000000",
                "canonical_taskset_json": f"fake-store/{taskset_id}.json",
                "task_input_json": canonical_json(tasks_input),
            }]
            requests = []
            attempts = []
            results = []
            task_rows = []
            observations = []
            emitted_variants = variants
            if mode in {"incomplete", "missing_terminal"} and call == 0:
                emitted_variants = variants[:1]
            for variant_index, variant in enumerate(variants):
                analysis_id = domain_hash(
                    "FAKE:CORE5:ANALYSIS", {
                        "call": call, "variant": variant,
                        "experiment": self.child["experiment_id"],
                    },
                )
                request_id = f"request-{analysis_id}"
                requests.append({
                    "request_id": request_id, "analysis_id": analysis_id,
                    "cell_id": f"child-cell-{call}",
                    "taskset_id": taskset_id, "taskset_hash": taskset_hash,
                    "exact_e0": "0", "variant": variant,
                    "numerical_mode": "EXACT_RATIONAL", "timeout_seconds": 4,
                    "retry_timeout_seconds": "", "source_analysis_id": "",
                    "request_status": (
                        "TERMINAL" if variant in emitted_variants else "PLANNED"
                    ),
                })
                if variant not in emitted_variants:
                    continue
                status = "COMPLETED"
                if mode in {"technical", "technical_outer_timeout"} and call == 0 and variant_index == 0:
                    status = "INTERNAL_CONFORMANCE_FAILURE"
                if mode in {"unknown", "unknown_outer_timeout"} and call == 0 and variant_index == 0:
                    status = "ALIEN_STATUS"
                if mode == "worker_timeout" and call == 7:
                    status = "TIMEOUT"
                outer_timeout = status == "TIMEOUT" or (
                    mode in {"technical_outer_timeout", "unknown_outer_timeout"}
                    and call == 0 and variant_index == 0
                )
                attempt_id = f"attempt-{analysis_id}"
                attempts.append({
                    "attempt_id": attempt_id, "analysis_id": analysis_id,
                    "attempt_number": 1, "parent_attempt_id": "",
                    "timeout_budget_seconds": 4, "solver_status": status,
                    "failure_origin": "ANALYZER_RESULT",
                    "outer_timeout": outer_timeout,
                    "solver_wall_seconds": .5, "solver_cpu_seconds": .4,
                    "worker_startup_seconds": .01,
                    "serialization_seconds": .01, "ipc_seconds": .01,
                    "payload_received": True, "worker_cleanup_status": "REAPED",
                    "worker_exitcode": 0, "worker_cleanup_seconds": .001,
                    "total_wall_seconds": .53, "exception_type": "",
                    "exception_message": "", "traceback": "",
                    "started_at_utc": "2026-07-19T00:00:00Z",
                })
                result = {
                    "analysis_id": analysis_id, "request_id": request_id,
                    "cell_id": f"child-cell-{call}",
                    "taskset_id": taskset_id, "taskset_hash": taskset_hash,
                    "generation_seed": 930435, "M": math_preimage["M"],
                    "task_n": math_preimage["task_n"], "utilization": "1/5",
                    "exact_e0": "0", "deadline_mode": "constrained",
                    "analysis_variant": variant, "method_role": "DIRECT",
                    "solver_status": status, "failure_origin": "ANALYZER_RESULT",
                    "certification_status": (
                        "CERTIFIED_TASKSET" if status == "COMPLETED"
                        else "NOT_CERTIFIED"
                    ),
                    "taskset_proven": status == "COMPLETED",
                    "first_failed_priority": "", "n_tasks_total": 1,
                    "n_tasks_evaluated": 1,
                    "n_tasks_candidate_found": 1 if status == "COMPLETED" else 0,
                    "n_tasks_certified": 1 if status == "COMPLETED" else 0,
                    "source_analysis_id": "", "source_vector_hash": "",
                    "target_carry_in_vector_hash": "",
                    "dependency_check_status": "NOT_APPLICABLE",
                    "fixed_carry_in_interface_status": "NOT_APPLICABLE",
                    "dominance_invariant_status": "NOT_APPLICABLE",
                    "dominance_violation_count": 0, "diagnostic_mode": False,
                    "final_attempt_id": attempt_id, "attempt_count": 1,
                    "timeout_budget_seconds": 4, "runtime_wall_seconds": .53,
                    "runtime_cpu_seconds": .4, "worker_startup_seconds": .01,
                    "serialization_seconds": .01, "ipc_seconds": .01,
                    "outer_timeout": outer_timeout,
                    "terminal_origin": "ANALYZER",
                }
                results.append(result)
                if status == "COMPLETED":
                    candidate = 2
                    if mode == "worker_semantic_mismatch" and call == 7:
                        candidate = 3
                    task_rows.append({
                        "analysis_id": analysis_id,
                        "cell_id": f"child-cell-{call}",
                        "taskset_id": taskset_id, "exact_e0": "0",
                        "analysis_variant": variant, "task_id": "0",
                        "priority_rank": 0, "C": 1, "D": 2, "T": 4, "P": 1,
                        "D_over_T": "1/2",
                        "task_solver_status": "CANDIDATE_FOUND",
                        "task_certification_status": "CERTIFIED",
                        "candidate_response_time": candidate,
                        "closing_w": candidate, "witness_h": 1,
                        "checked_w_count": 2, "checked_h_count": 3,
                        "checked_q_count": 4, "envelope_call_count": 5,
                        "failure_reason": "", "carry_in_vector_hash": "",
                    })
                observation = {
                    "attempt_id": attempt_id, "analysis_id": analysis_id,
                    "peak_rss_kib": 100, "peak_rss_scope": "CHILD_PROCESS",
                    "peak_rss_unit": "KiB", "observation_status": "AVAILABLE",
                    "unavailability_reason": "",
                }
                if mode == "rss_missing" and call == 0 and variant_index == 0:
                    observation.update({
                        "peak_rss_kib": "UNAVAILABLE",
                        "peak_rss_scope": "UNAVAILABLE",
                        "peak_rss_unit": "UNAVAILABLE",
                        "observation_status": "TECHNICAL_UNAVAILABLE",
                        "unavailability_reason": "MISSING_TEST_SAMPLE",
                    })
                observations.append(observation)

            if mode == "extra_terminal" and call == 0:
                extra = dict(results[0])
                extra["analysis_id"] = "extra-analysis"
                extra["request_id"] = "extra-request"
                extra["final_attempt_id"] = "extra-attempt"
                results.append(extra)
            failures = []
            if mode == "p0" and call == 0:
                failures.append({
                    "severity": "P0", "stage": "FAKE_CHILD",
                    "analysis_id": requests[0]["analysis_id"],
                    "cell_id": requests[0]["cell_id"],
                    "taskset_id": taskset_id, "variant": variants[0],
                    "code": "FAKE_CHILD_P0", "detail": "fake child P0",
                    "traceback": "", "failure_input": "fake-child.json",
                })
            write_csv(root / "generated_tasksets.csv", GENERATED_COLUMNS, generated)
            write_csv(root / "analysis_requests.csv", REQUEST_COLUMNS, requests)
            write_csv(root / "analysis_attempts.csv", ATTEMPT_COLUMNS, attempts)
            write_csv(root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
            write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, task_rows)
            write_csv(root / "attempt_resource_observations.csv", RESOURCE_OBSERVATION_COLUMNS, observations)
            write_csv(root / "failures.csv", FAILURE_COLUMNS, failures)
            counts = Counter(row["solver_status"] for row in results)
            if mode == "status_counts_mismatch" and call == 0:
                counts = Counter({"COMPLETED": 999})
            requested = len(requests)
            terminal = len(results)
            if mode == "incomplete" and call == 0:
                requested += 1
            return RunOutcome(
                root, requested, terminal, dict(counts),
                mode == "stopped" and call == 0,
            )

    monkeypatch.setattr(
        scalability_module, "prepare_service_curve", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(scalability_module, "ResourceExecutionEngine", FakeEngine)
    return FakeEngine


@pytest.fixture
def completed_core5(tmp_path, monkeypatch):
    fake = _install_fake_pipeline(monkeypatch)
    outcome = Core5ScalabilityRunner(_config(tmp_path)).run()
    assert len(fake.calls) == 8
    assert not outcome.stopped
    return outcome


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("stopped", "CHILD_STOPPED"),
        ("incomplete", "CHILD_INCOMPLETE"),
        ("missing_terminal", "CHILD_INCOMPLETE"),
        ("extra_terminal", "CHILD_INCOMPLETE"),
        ("p0", "CHILD_P0_FAILURE"),
        ("technical", "CHILD_TECHNICAL_TERMINAL"),
        ("technical_outer_timeout", "CHILD_TECHNICAL_TERMINAL"),
        ("unknown", "CHILD_TECHNICAL_TERMINAL"),
        ("unknown_outer_timeout", "CHILD_TECHNICAL_TERMINAL"),
        ("rss_missing", "RESOURCE_OBSERVATION_CONTRACT_FAILURE"),
        ("status_counts_mismatch", "CHILD_CONTRACT_FAILURE"),
    ],
)
def test_child_failure_propagates_immediately_and_stops_later_cells(
    tmp_path, monkeypatch, mode, code,
):
    fake = _install_fake_pipeline(monkeypatch, mode=mode)
    outcome = Core5ScalabilityRunner(_config(tmp_path)).run()
    assert outcome.stopped
    assert len(fake.calls) == 1
    checkpoint = json.loads(
        (outcome.output_root / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["phase"] == "STOPPED"
    assert checkpoint["schema"] == CORE5_CHECKPOINT_SCHEMA
    assert checkpoint["stop_requested"] is True
    if mode == "technical_outer_timeout":
        assert checkpoint["technical_failure_count"] == 1
    assert any(row["code"] == code for row in read_csv(outcome.output_root / "failures.csv"))
    assert not (outcome.output_root / "scalability_summary.csv").exists()
    validate_core5_hash_manifest(outcome.output_root, require_completed_files=False)


@pytest.mark.parametrize(
    "mode",
    ["worker_hash_mismatch", "worker_semantic_mismatch"],
)
def test_worker_mathematical_or_semantic_mismatch_is_top_level_p0(
    tmp_path, monkeypatch, mode,
):
    _install_fake_pipeline(monkeypatch, mode=mode)
    outcome = Core5ScalabilityRunner(_config(tmp_path)).run()
    assert outcome.stopped
    checks = read_csv(outcome.output_root / "worker_semantic_checks.csv")
    expected = (
        "WORKER_MATHEMATICAL_INPUT_MISMATCH"
        if mode == "worker_hash_mismatch"
        else "WORKER_SEMANTIC_MISMATCH"
    )
    assert any(row["status"] == expected for row in checks)
    assert json.loads(
        (outcome.output_root / "checkpoint.json").read_text(encoding="utf-8")
    )["phase"] == "STOPPED"


def test_worker_timeout_is_censored_not_equal_or_mismatch(tmp_path, monkeypatch):
    _install_fake_pipeline(monkeypatch, mode="worker_timeout")
    outcome = Core5ScalabilityRunner(_config(tmp_path)).run()
    assert not outcome.stopped
    checks = read_csv(outcome.output_root / "worker_semantic_checks.csv")
    assert {row["status"] for row in checks} == {"TIMEOUT_CENSORED"}


def test_missing_worker_terminal_produces_explicit_p0_pair_check(completed_core5):
    from experiments.v9_3.core5_aggregation import _worker_semantic_checks

    root = completed_core5.output_root
    worker_cells = [
        row for row in read_csv(root / "scalability_cells.csv")
        if row["scaling_axis"] == "worker_count" and row["worker_count"] == "2"
    ]
    missing_id = json.loads(worker_cells[0]["analysis_ids_json"])[0]
    results = [
        row for row in read_csv(root / "per_taskset_results.csv")
        if row["analysis_id"] != missing_id
    ]
    write_csv(root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
    checks = _worker_semantic_checks(root)
    assert any(row["status"] == "MISSING_WORKER_PAIR" for row in checks)


def test_worker_technical_terminal_is_never_semantically_equal(completed_core5):
    from experiments.v9_3.core5_aggregation import _worker_semantic_checks

    root = completed_core5.output_root
    worker_ids = {
        analysis_id
        for cell in read_csv(root / "scalability_cells.csv")
        if cell["scaling_axis"] == "worker_count"
        for analysis_id in json.loads(cell["analysis_ids_json"])
    }
    results = read_csv(root / "per_taskset_results.csv")
    for row in results:
        if row["analysis_id"] in worker_ids:
            row["solver_status"] = "NUMERIC_ERROR"
            row["outer_timeout"] = "True"
    write_csv(root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
    checks = _worker_semantic_checks(root)
    assert {row["status"] for row in checks} == {"TECHNICAL_FAILURE"}


def test_fake_success_closes_exact_8_16_20_v2_and_manifest(completed_core5):
    outcome = completed_core5
    metadata = json.loads(
        (outcome.output_root / "run_metadata.json").read_text(encoding="utf-8")
    )
    checkpoint = json.loads(
        (outcome.output_root / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert metadata["schema"] == CORE5_RUN_SCHEMA
    assert checkpoint["schema"] == CORE5_CHECKPOINT_SCHEMA
    assert checkpoint["phase"] == "COMPLETED"
    assert checkpoint["planned_scalability_cell_count"] == 8
    assert checkpoint["planned_analysis_count"] == 16
    assert checkpoint["hard_analysis_limit"] == 20
    assert checkpoint["actual_terminal_count"] == 16
    assert len(read_csv(outcome.output_root / "child_outcomes.csv")) == 8
    validate_core5_artifact_contract(outcome.output_root)


def test_summary_all_unavailable_search_counters_are_not_zero(completed_core5):
    from experiments.v9_3.core5_aggregation import aggregate_core5

    root = completed_core5.output_root
    tasks = read_csv(root / "per_task_results.csv")
    for row in tasks:
        for field in (
            "checked_w_count", "checked_h_count", "checked_q_count",
            "envelope_call_count",
        ):
            row[field] = "UNAVAILABLE"
    write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, tasks)
    summary = aggregate_core5(root)
    assert all(
        group["checked_w_final_attempt_total"] == "UNAVAILABLE"
        and group["search_counter_observation_status"] == "UNAVAILABLE"
        for group in summary["groups"]
    )


def test_summary_marks_partial_counter_aggregation(completed_core5):
    from experiments.v9_3.core5_aggregation import aggregate_core5

    root = completed_core5.output_root
    tasks = read_csv(root / "per_task_results.csv")
    extra = dict(tasks[0])
    extra["task_id"] = "partial-extra"
    for field in (
        "checked_w_count", "checked_h_count", "checked_q_count",
        "envelope_call_count",
    ):
        extra[field] = "UNAVAILABLE"
    write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, [*tasks, extra])
    summary = aggregate_core5(root)
    assert any(
        group["search_counter_observation_status"] == "PARTIAL"
        and group["search_counter_available_analysis_count"] == 1
        for group in summary["groups"]
    )


@pytest.mark.parametrize(
    ("target", "mutation", "match"),
    [
        ("run_metadata.json", {"schema": "BROKEN"}, "metadata schema"),
        ("checkpoint.json", {"schema": "BROKEN"}, "checkpoint schema"),
    ],
)
def test_corrupt_v2_metadata_or_checkpoint_is_rejected(
    completed_core5, target, mutation, match,
):
    path = completed_core5.output_root / target
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(mutation)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(Core5ContractError, match=match):
        validate_core5_artifact_contract(completed_core5.output_root)


def test_missing_manifest_and_tampered_hash_are_rejected(completed_core5):
    root = completed_core5.output_root
    manifest = root / "file_hashes.sha256"
    original = manifest.read_text(encoding="utf-8")
    manifest.unlink()
    with pytest.raises(Core5ContractError, match="manifest|file_hashes"):
        validate_core5_artifact_contract(root)
    manifest.write_text(original, encoding="utf-8")
    summary = root / "scalability_summary.csv"
    summary.write_text(summary.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
    with pytest.raises(Core5ContractError, match="hash mismatch"):
        validate_core5_artifact_contract(root)


@pytest.mark.parametrize(
    ("name", "columns", "identity"),
    [
        ("analysis_requests.csv", REQUEST_COLUMNS, "analysis request"),
        ("analysis_attempts.csv", ATTEMPT_COLUMNS, "analysis attempt"),
        ("per_taskset_results.csv", TASKSET_RESULT_COLUMNS, "terminal"),
        (
            "attempt_resource_observations.csv", RESOURCE_OBSERVATION_COLUMNS,
            "resource observation",
        ),
    ],
)
def test_completed_contract_rejects_duplicate_primary_rows(
    completed_core5, name, columns, identity,
):
    root = completed_core5.output_root
    rows = read_csv(root / name)
    write_csv(root / name, columns, [*rows, rows[0]])
    with pytest.raises(Core5ContractError, match=f"duplicate {identity}"):
        validate_core5_artifact_contract(root)


def test_completed_contract_rejects_missing_terminal(completed_core5):
    root = completed_core5.output_root
    results = read_csv(root / "per_taskset_results.csv")
    write_csv(
        root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results[:-1]
    )
    with pytest.raises(Core5ContractError, match="request/terminal"):
        validate_core5_artifact_contract(root)


def test_completed_contract_rejects_extra_terminal(completed_core5):
    root = completed_core5.output_root
    results = read_csv(root / "per_taskset_results.csv")
    extra = dict(results[0])
    extra["analysis_id"] = "unplanned-extra-analysis"
    write_csv(
        root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS,
        [*results, extra],
    )
    with pytest.raises(Core5ContractError, match="request/terminal|extra terminal"):
        validate_core5_artifact_contract(root)


def test_completed_contract_rejects_header_mismatch(completed_core5):
    path = completed_core5.output_root / "analysis_requests.csv"
    path.write_text("not,the,required,header\n", encoding="utf-8")
    with pytest.raises(Core5ContractError, match="header mismatch"):
        validate_core5_artifact_contract(completed_core5.output_root)


def test_manifest_rejects_extra_file_symlink_and_unsafe_path(completed_core5):
    root = completed_core5.output_root
    extra = root / "unexpected.txt"
    extra.write_text("extra\n", encoding="utf-8")
    with pytest.raises(Core5ContractError, match="file set mismatch"):
        validate_core5_hash_manifest(root, require_completed_files=True)
    extra.unlink()
    link = root / "unsafe-link"
    link.symlink_to(root / "run_metadata.json")
    with pytest.raises(Core5ContractError, match="symlink"):
        validate_core5_hash_manifest(root, require_completed_files=True)
    link.unlink()
    manifest = root / "file_hashes.sha256"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"{'0' * 64}  ../escape\n",
        encoding="utf-8",
    )
    with pytest.raises(Core5ContractError, match="unsafe path"):
        validate_core5_hash_manifest(root, require_completed_files=True)


def test_empty_partial_and_stopped_roots_are_not_analyzable(tmp_path, monkeypatch):
    from experiments.v9_3.core5_aggregation import analyze_core5_artifacts

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Core5ContractError):
        analyze_core5_artifacts(empty)
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "run_metadata.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(Core5ContractError):
        analyze_core5_artifacts(partial)
    _install_fake_pipeline(monkeypatch, mode="stopped")
    stopped = Core5ScalabilityRunner(_config(tmp_path / "stopped")).run()
    with pytest.raises(Core5ContractError, match="completed"):
        analyze_core5_artifacts(stopped.output_root)


def test_standalone_analyzer_cli_rejects_empty_and_accepts_completed(
    tmp_path, completed_core5,
):
    script = ROOT / "scripts/analyze_v9_3_core5.py"
    empty = tmp_path / "cli-empty"
    empty.mkdir()
    rejected = subprocess.run(
        [sys.executable, str(script), str(empty)], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    assert rejected.returncode != 0
    accepted = subprocess.run(
        [sys.executable, str(script), str(completed_core5.output_root)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["stopped"] is False
    validate_core5_artifact_contract(completed_core5.output_root)


def test_completed_checkpoint_is_written_after_manifest_validation(
    tmp_path, monkeypatch,
):
    _install_fake_pipeline(monkeypatch)
    events = []
    original_checkpoint = Core5ScalabilityRunner._write_checkpoint
    original_write = scalability_module.write_core5_hash_manifest
    original_validate = scalability_module.validate_core5_hash_manifest

    def checkpoint(self, **kwargs):
        events.append(f"checkpoint:{kwargs['phase']}")
        return original_checkpoint(self, **kwargs)

    def write_manifest(root):
        events.append("manifest:write")
        return original_write(root)

    def validate_manifest(root, *, require_completed_files):
        events.append(f"manifest:validate:{require_completed_files}")
        return original_validate(
            root, require_completed_files=require_completed_files
        )

    monkeypatch.setattr(Core5ScalabilityRunner, "_write_checkpoint", checkpoint)
    monkeypatch.setattr(
        scalability_module, "write_core5_hash_manifest", write_manifest
    )
    monkeypatch.setattr(
        scalability_module, "validate_core5_hash_manifest", validate_manifest
    )
    Core5ScalabilityRunner(_config(tmp_path)).run()
    tail = events[-4:]
    assert tail == [
        "checkpoint:FINALIZING", "manifest:write",
        "manifest:validate:True", "checkpoint:COMPLETED",
    ]
