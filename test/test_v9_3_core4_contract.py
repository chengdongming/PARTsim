from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import asap_block_rta_v9_3 as rta_core
from experiments.v9_3.config import (
    ConfigError,
    canonical_json,
    domain_hash,
    fraction_text,
    load_config,
    validate_config,
)
from experiments.v9_3.core4_aggregation import (
    aggregate_core4,
    analyze_core4_artifacts,
)
from experiments.v9_3.core4_contract import (
    CORE4_CHECKPOINT_SCHEMA,
    Core4ContractError,
    core4_analysis_input_hash,
    validate_core4_artifact_contract,
    validate_core4_pairing,
)
import experiments.v9_3.core4_sensitivity as sensitivity_module
from experiments.v9_3.core4_sensitivity import Core4SensitivityRunner
from experiments.v9_3.execution_engine import RunOutcome
from experiments.v9_3.monotonicity import compare_paired_analyses
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
from experiments.v9_3.taskset_store import ServiceCurveMaterial, StoredTaskset
from v9_3_core4_helpers import make_pair_fixture, write_pair_fixture


ROOT = Path(__file__).resolve().parents[1]


def _comparison_result(status: str = "COMPLETED") -> dict[str, object]:
    return {
        "solver_status": status,
        "taskset_proven": False,
        "taskset_hash": "same",
        "outer_timeout": False,
    }


@pytest.mark.parametrize(
    "status",
    ["NUMERIC_ERROR", "INTERNAL_CONFORMANCE_FAILURE", "INVALID_RESULT", "UNKNOWN_TERMINAL"],
)
def test_technical_and_unknown_terminals_are_fail_closed(status):
    comparison = compare_paired_analyses(
        _comparison_result(status), _comparison_result(), [], [],
        direction="RESOURCE_INCREASE",
    )
    assert comparison["monotonicity_status"] == "TECHNICAL_FAILURE"


def test_known_non_applicable_dependency_is_explicitly_not_comparable():
    comparison = compare_paired_analyses(
        _comparison_result("NOT_APPLICABLE_DEPENDENCY"), _comparison_result(),
        [], [], direction="LOC_DOMINANCE",
    )
    assert comparison["monotonicity_status"] == "NOT_COMPARABLE"


@pytest.mark.parametrize(
    ("direction", "positive"),
    [
        ("RESOURCE_INCREASE", (False, True, 5, 4)),
        ("RESOURCE_INCREASE", (False, True, 5, 4)),
        ("COST_INCREASE", (True, False, 4, None)),
        ("LOC_DOMINANCE", (False, True, 5, 4)),
    ],
    ids=("initial-energy", "service-curve", "power-scale", "method"),
)
def test_four_axis_directions_keep_positive_equal_and_reverse(direction, positive):
    left_proven, right_proven, left_candidate, right_candidate = positive

    def task(value):
        return [] if value is None else [{
            "task_id": "0", "task_solver_status": "CANDIDATE_FOUND",
            "candidate_response_time": value,
        }]

    improved = compare_paired_analyses(
        {**_comparison_result(), "taskset_proven": left_proven},
        {**_comparison_result(), "taskset_proven": right_proven},
        task(left_candidate), task(right_candidate), direction=direction,
    )
    equal = compare_paired_analyses(
        {**_comparison_result(), "taskset_proven": True},
        {**_comparison_result(), "taskset_proven": True},
        task(5), task(5), direction=direction,
    )
    if direction == "COST_INCREASE":
        reverse = compare_paired_analyses(
            _comparison_result(),
            {**_comparison_result(), "taskset_proven": True},
            task(5), task(4), direction=direction,
        )
    else:
        reverse = compare_paired_analyses(
            {**_comparison_result(), "taskset_proven": True},
            _comparison_result(), task(4), task(5), direction=direction,
        )
    assert improved["monotonicity_status"] == "MONOTONICITY_HOLDS"
    assert equal["monotonicity_status"] == "EQUAL"
    assert reverse["monotonicity_status"] == "MONOTONICITY_VIOLATION"


def _sensitivity_power_hash(tasks):
    return domain_hash(
        "ASAP_BLOCK:V9.3:SENSITIVITY_POWER_VECTOR:v1",
        [{"task_id": row["task_id"], "P": row["P"]} for row in tasks],
    )


def _mutate_pair_fixture(fixture, case):
    requests = fixture["sensitivity_requests"]
    results = fixture["per_taskset_results"]
    tasks = fixture["per_task_results"]
    if case == "M":
        results[1]["M"] = 8
    elif case == "priority":
        tasks[1]["priority_rank"] = 9
    elif case == "non_target_e0":
        fixture = make_pair_fixture("power_scale")
        fixture["sensitivity_requests"][1]["exact_e0"] = "1"
        fixture["per_taskset_results"][1]["exact_e0"] = "1"
        fixture["sensitivity_requests"][1]["analysis_input_hash"] = core4_analysis_input_hash(
            fixture["sensitivity_requests"][1]
        )
        return fixture
    elif case == "non_target_service":
        requests[1]["service_curve_identity"] = "different-service"
        requests[1]["service_curve_values_json"] = canonical_json(["0", "2"])
        requests[1]["analysis_input_hash"] = core4_analysis_input_hash(requests[1])
    elif case == "non_target_power":
        transformed = json.loads(requests[1]["analysis_task_input_json"])
        transformed[0]["P"] = "2"
        requests[1]["power_scale"] = "2"
        requests[1]["analysis_task_input_json"] = canonical_json(transformed)
        requests[1]["analysis_power_hash"] = _sensitivity_power_hash(transformed)
        requests[1]["analysis_input_hash"] = core4_analysis_input_hash(requests[1])
        tasks[1]["P"] = "2"
    elif case == "analysis_input_hash":
        requests[1]["analysis_input_hash"] = "bad-input-hash"
    elif case == "duplicate_request":
        requests.append(deepcopy(requests[0]))
    elif case == "missing_terminal":
        results.pop()
        tasks.pop()
    elif case == "unavailable_has_terminal":
        fixture = make_pair_fixture("service_curve", second_unavailable=True)
        terminal = deepcopy(fixture["per_taskset_results"][0])
        terminal["analysis_id"] = fixture["sensitivity_requests"][1]["analysis_id"]
        terminal["request_id"] = "unexpected-unavailable"
        fixture["per_taskset_results"].append(terminal)
        return fixture
    elif case == "extra_terminal":
        terminal = deepcopy(results[1])
        terminal["analysis_id"] = "extra-terminal"
        results.append(terminal)
    elif case == "available_dependency_unavailable_status":
        results[0]["solver_status"] = "DEPENDENCY_UNAVAILABLE"
    elif case == "conflicting_duplicate":
        terminal = deepcopy(results[1])
        terminal["taskset_proven"] = False
        results.append(terminal)
    elif case == "swapped_levels":
        requests[0]["level_index"], requests[1]["level_index"] = 1, 0
    elif case == "paired_id":
        requests[1]["paired_analysis_ids"] = "[]"
    else:
        raise AssertionError(case)
    return fixture


@pytest.mark.parametrize(
    "case",
    [
        "M", "priority", "non_target_e0", "non_target_service",
        "non_target_power", "analysis_input_hash", "duplicate_request",
        "missing_terminal", "unavailable_has_terminal", "extra_terminal",
        "available_dependency_unavailable_status", "conflicting_duplicate",
        "swapped_levels", "paired_id",
    ],
)
def test_pairing_and_terminal_corruption_fails_closed(tmp_path, case):
    fixture = _mutate_pair_fixture(make_pair_fixture(), case)
    write_pair_fixture(tmp_path, fixture)
    with pytest.raises(Core4ContractError):
        aggregate_core4(tmp_path)


def test_undeclared_unavailable_row_is_rejected_by_pairing_validator(tmp_path):
    fixture = make_pair_fixture()
    request = fixture["sensitivity_requests"][1]
    request["availability"] = "UNAVAILABLE"
    request["analysis_input_hash"] = core4_analysis_input_hash(request)
    fixture["analysis_requests"].pop()
    fixture["per_taskset_results"].pop()
    fixture["per_task_results"].pop()
    write_pair_fixture(tmp_path, fixture)
    with pytest.raises(Core4ContractError, match="availability.*ordered level"):
        validate_core4_pairing(tmp_path)


def test_method_axis_reverse_and_replacement_are_rejected():
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    for variants in (
        ["LOC_THETA_LOC", "CW_THETA_CW"],
        ["CW_D", "LOC_D"],
    ):
        bad = deepcopy(config)
        bad["analysis"]["variants"] = variants
        bad["sensitivity"]["axes"]["method"]["variants"] = variants
        with pytest.raises(ConfigError, match="CORE-4 method variants"):
            validate_config(bad, expected_core="CORE-4")


def test_unavailable_is_null_in_summary_and_plot_and_has_separate_counts(tmp_path):
    write_pair_fixture(
        tmp_path, make_pair_fixture("service_curve", second_unavailable=True)
    )
    summary = aggregate_core4(tmp_path)
    unavailable = next(
        row for row in summary["level_summaries"] if int(row["level_index"]) == 1
    )
    assert unavailable["certification_ratio"] is None
    assert unavailable["completed_only_ratio"] is None
    assert unavailable["candidate_count"] is None
    assert unavailable["candidate_mean"] is None
    plot = read_csv(tmp_path / "core4_plot_data.csv")
    unavailable_plot = [row for row in plot if row["level_index"] == "1"]
    assert unavailable_plot
    assert all(row["value"] == "" for row in unavailable_plot)
    assert summary["planned_sensitivity_row_count"] == 2
    assert summary["available_solver_request_count"] == 1
    assert summary["expected_terminal_count"] == 1
    assert summary["actual_terminal_count"] == 1
    assert summary["dependency_unavailable_row_count"] == 1
    assert summary["technical_failure_count"] == 0


def test_describe_uses_six_exact_count_fields_and_no_legacy_maximum():
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    description = Core4SensitivityRunner(config).describe()
    assert description["planned_sensitivity_row_count"] == 14
    assert description["available_solver_request_count"] == 12
    assert description["expected_terminal_count"] == 12
    assert description["actual_terminal_count"] == 0
    assert description["dependency_unavailable_row_count"] == 2
    assert description["technical_failure_count"] == 0
    assert "maximum_solver_requests" not in description


def test_empty_and_partial_analyzer_roots_are_rejected(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Core4ContractError, match="run metadata"):
        analyze_core4_artifacts(empty)

    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "partial")
    runner = Core4SensitivityRunner(config)
    runner._initialize(False)
    fixture = make_pair_fixture()
    write_csv(
        runner.root / "sensitivity_requests.csv",
        sensitivity_module.SENSITIVITY_REQUEST_COLUMNS,
        fixture["sensitivity_requests"],
    )
    with pytest.raises(Core4ContractError, match="completed non-stopped"):
        analyze_core4_artifacts(runner.root)
    with pytest.raises(ConfigError, match="partial top-level"):
        Core4SensitivityRunner(config)._initialize(True)


def test_resume_config_and_checkpoint_schema_mismatch_are_rejected(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    runner = Core4SensitivityRunner(config)
    runner._initialize(False)
    changed = deepcopy(config)
    changed["analysis"]["timeout_seconds"] = 5
    with pytest.raises(ConfigError, match="configuration hash mismatch"):
        Core4SensitivityRunner(changed)._initialize(True)
    checkpoint_path = runner.root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["schema"] = "UNKNOWN"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(ConfigError, match="checkpoint schema"):
        Core4SensitivityRunner(config)._initialize(True)


def test_resume_requires_the_persisted_run_config(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    runner = Core4SensitivityRunner(config)
    runner._initialize(False)
    (runner.root / "run_config.yaml").unlink()
    with pytest.raises(ConfigError, match="run_config.yaml is missing"):
        Core4SensitivityRunner(config)._initialize(True)


def _stored_taskset(tmp_path: Path) -> StoredTaskset:
    payload = tuple({
        "task_id": str(index), "source_name": f"task-{index}",
        "priority_rank": index, "C": 1, "D": 5, "T": 7, "P": "1",
        "D_over_T": "5/7", "workload": "idle", "arrival_offset": 0,
    } for index in range(10))
    tasks = tuple(
        rta_core.V93Task(str(index), 1, 5, 7, Fraction(1)) for index in range(10)
    )
    priority_hash = domain_hash(
        "ASAP_BLOCK:V9.3:PRIORITY_VECTOR:v1",
        [{"task_id": row["task_id"], "priority_rank": row["priority_rank"]} for row in payload],
    )
    power_hash = domain_hash(
        "ASAP_BLOCK:V9.3:POWER_VECTOR:v1",
        [{"task_id": row["task_id"], "P": row["P"]} for row in payload],
    )
    return StoredTaskset(
        "base-taskset", "generation", 0, 7, "base-semantic-hash",
        priority_hash, power_hash, Fraction(1, 5), Fraction(10, 7),
        4, 10, "constrained", tasks, payload, 0.0, "service-base",
        tmp_path / "taskset.json",
    )


def _install_fake_pipeline(monkeypatch, tmp_path, *, mode="success", status="COMPLETED"):
    stored = _stored_taskset(tmp_path)
    cell = SimpleNamespace(
        processors=4, task_count=10, utilization=Fraction(1, 5),
        generation_id="generation",
    )
    service = ServiceCurveMaterial(
        (Fraction(0), Fraction(1)), "service-base", "{}", tmp_path / "service.yml"
    )

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def get_or_create(self, _cell, _index):
            return stored

    class FakeEngine:
        calls = []

        def __init__(self, child, **kwargs):
            self.child = child
            self.scale = kwargs["store_override"].power_scale

        def run(self, **kwargs):
            call = len(self.calls)
            self.calls.append(self.child["experiment_id"])
            root = Path(self.child["execution"]["output_root"])
            variants = list(self.child["analysis"]["variants"])
            emitted = variants[:-1] if mode == "terminal_mismatch" else variants
            results = []
            requests = []
            task_rows = []
            for variant_index, variant in enumerate(emitted):
                aid = f"child-{call}-{variant}"
                terminal_status = status if call == 0 and variant_index == 0 else "COMPLETED"
                results.append({
                    "analysis_id": aid, "request_id": f"request-{aid}",
                    "taskset_id": stored.taskset_id,
                    "taskset_hash": stored.semantic_hash,
                    "M": 4, "task_n": 10,
                    "exact_e0": self.child["energy"]["initial_energy_values"][0],
                    "analysis_variant": variant, "solver_status": terminal_status,
                    "taskset_proven": terminal_status == "COMPLETED",
                    "runtime_wall_seconds": "0.01", "outer_timeout": False,
                })
                requests.append({
                    "request_id": f"request-{aid}", "analysis_id": aid,
                    "cell_id": f"cell-{call}", "taskset_id": stored.taskset_id,
                    "taskset_hash": stored.semantic_hash,
                    "exact_e0": self.child["energy"]["initial_energy_values"][0],
                    "variant": variant, "numerical_mode": "EXACT_RATIONAL",
                    "timeout_seconds": 4, "retry_timeout_seconds": 8,
                    "source_analysis_id": "", "request_status": "TERMINAL",
                })
                if terminal_status == "COMPLETED":
                    for task in stored.task_payload:
                        task_rows.append({
                            "analysis_id": aid, "taskset_id": stored.taskset_id,
                            "task_id": task["task_id"],
                            "priority_rank": task["priority_rank"],
                            "C": task["C"], "D": task["D"], "T": task["T"],
                            "P": fraction_text(Fraction(task["P"]) * self.scale),
                            "task_solver_status": "CANDIDATE_FOUND",
                            "candidate_response_time": 4,
                        })
            write_csv(root / "analysis_requests.csv", REQUEST_COLUMNS, requests)
            write_csv(root / "analysis_attempts.csv", ATTEMPT_COLUMNS, [])
            write_csv(root / "per_taskset_results.csv", TASKSET_RESULT_COLUMNS, results)
            write_csv(root / "per_task_results.csv", TASK_RESULT_COLUMNS, task_rows)
            write_csv(root / "generated_tasksets.csv", GENERATED_COLUMNS, [stored.generated_row()])
            failures = []
            if mode == "p0_failure" and call == 0:
                failures.append({
                    "severity": "P0", "stage": "ANALYSIS",
                    "analysis_id": results[0]["analysis_id"], "cell_id": "",
                    "taskset_id": stored.taskset_id, "variant": variants[0],
                    "code": "FakeP0", "detail": "fake child P0", "traceback": "",
                    "failure_input": "fake.json",
                })
            if mode != "missing_failures":
                write_csv(root / "failures.csv", FAILURE_COLUMNS, failures)
            requested = len(variants)
            if mode == "requested_plan_mismatch":
                requested += 1
            return RunOutcome(
                root, requested, len(results),
                {row["solver_status"]: 1 for row in results},
                mode == "stopped" and call == 0,
            )

    monkeypatch.setattr(sensitivity_module, "TasksetStore", FakeStore)
    monkeypatch.setattr(sensitivity_module, "ExecutionEngine", FakeEngine)
    monkeypatch.setattr(sensitivity_module, "expand_cells", lambda _config: (cell,))
    monkeypatch.setattr(
        Core4SensitivityRunner,
        "_service_materials",
        lambda self: (
            service,
            {"repository-default-service-v1": service},
            {
                "repository-default-service-v1": "FIRST_LEVEL",
                "second-formal-service-curve": "DEPENDENCY_UNAVAILABLE",
            },
        ),
    )
    return FakeEngine


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("stopped", "COMPLETED"),
        ("terminal_mismatch", "COMPLETED"),
        ("requested_plan_mismatch", "COMPLETED"),
        ("p0_failure", "COMPLETED"),
        ("missing_failures", "COMPLETED"),
        ("technical", "NUMERIC_ERROR"),
        ("technical", "INTERNAL_CONFORMANCE_FAILURE"),
        ("technical", "INVALID_RESULT"),
        ("technical", "UNKNOWN_TERMINAL"),
        ("technical", "DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_child_failure_modes_stop_before_the_next_level(monkeypatch, tmp_path, mode, status):
    fake_engine = _install_fake_pipeline(
        monkeypatch, tmp_path, mode=mode, status=status
    )
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    outcome = Core4SensitivityRunner(config).run()
    assert len(fake_engine.calls) == 1
    assert outcome.stopped is True
    assert outcome.planned_sensitivity_row_count == 14
    assert outcome.available_solver_request_count == 12
    assert outcome.expected_terminal_count == 12
    assert outcome.dependency_unavailable_row_count == 2
    assert outcome.technical_failure_count >= 1
    assert not (outcome.output_root / "sensitivity_summary.json").exists()
    assert not (outcome.output_root / "core4_plot_data.csv").exists()
    checkpoint = json.loads((outcome.output_root / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["schema"] == CORE4_CHECKPOINT_SCHEMA
    assert checkpoint["phase"] == "STOPPED"
    assert checkpoint["stop_requested"] is True
    assert list((outcome.output_root / "failure_inputs").glob("*.json"))


def test_fake_success_pipeline_closes_counts_artifacts_hashes_and_analyzer(
    monkeypatch, tmp_path
):
    fake_engine = _install_fake_pipeline(monkeypatch, tmp_path)
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    outcome = Core4SensitivityRunner(config).run()
    assert len(fake_engine.calls) == 7
    assert outcome.stopped is False
    assert (
        outcome.planned_sensitivity_row_count,
        outcome.available_solver_request_count,
        outcome.expected_terminal_count,
        outcome.actual_terminal_count,
        outcome.dependency_unavailable_row_count,
        outcome.technical_failure_count,
    ) == (14, 12, 12, 12, 2, 0)
    validate_core4_artifact_contract(outcome.output_root)
    rebuilt = analyze_core4_artifacts(outcome.output_root)
    assert rebuilt["actual_terminal_count"] == 12

    result_path = outcome.output_root / "per_taskset_results.csv"
    results = read_csv(result_path)
    results[0]["runtime_wall_seconds"] = "999"
    write_csv(result_path, TASKSET_RESULT_COLUMNS, results)
    with pytest.raises(Core4ContractError, match="file hash mismatch"):
        validate_core4_artifact_contract(outcome.output_root)


@pytest.fixture
def completed_core4_run(monkeypatch, tmp_path):
    fake_engine = _install_fake_pipeline(monkeypatch, tmp_path)
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    outcome = Core4SensitivityRunner(config).run()
    assert len(fake_engine.calls) == 7
    assert outcome.stopped is False
    return outcome, config


def test_completed_valid_manifest_is_accepted_and_excludes_commit_markers(
    completed_core4_run,
):
    outcome, _config = completed_core4_run
    validate_core4_artifact_contract(outcome.output_root)
    manifest = (outcome.output_root / "file_hashes.sha256").read_text(
        encoding="utf-8"
    )
    assert "  checkpoint.json\n" not in manifest
    assert "  file_hashes.sha256\n" not in manifest


def test_completed_missing_manifest_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    (outcome.output_root / "file_hashes.sha256").unlink()
    with pytest.raises(Core4ContractError, match="file_hashes.sha256 is missing"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_empty_manifest_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    (outcome.output_root / "file_hashes.sha256").write_text("", encoding="utf-8")
    with pytest.raises(Core4ContractError, match="file_hashes.sha256 is empty"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_manifest_missing_required_member_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    member = outcome.output_root / "sensitivity_summary.json"
    member.unlink()
    manifest = outcome.output_root / "file_hashes.sha256"
    lines = [
        line for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.endswith("  sensitivity_summary.json")
    ]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(Core4ContractError, match="omits required completed files"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_manifest_declared_missing_file_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    (outcome.output_root / "core4_plot_data.csv").unlink()
    with pytest.raises(Core4ContractError, match="missing or non-regular file"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_manifest_malformed_digest_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    manifest = outcome.output_root / "file_hashes.sha256"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    lines[0] = "not-a-sha256  " + lines[0].split("  ", 1)[1]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(Core4ContractError, match="invalid row"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_manifest_rejects_symbolic_link_member(completed_core4_run):
    outcome, _config = completed_core4_run
    member = outcome.output_root / "core4_plot_data.csv"
    member.unlink()
    member.symlink_to(outcome.output_root / "sensitivity_summary.csv")
    with pytest.raises(Core4ContractError, match="symbolic link|non-regular file"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_duplicate_manifest_path_is_rejected(completed_core4_run):
    outcome, _config = completed_core4_run
    manifest = outcome.output_root / "file_hashes.sha256"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    manifest.write_text("\n".join([*lines, lines[0]]) + "\n", encoding="utf-8")
    with pytest.raises(Core4ContractError, match="duplicate path"):
        validate_core4_artifact_contract(outcome.output_root)


@pytest.mark.parametrize("unsafe_path", ["/absolute/path", "../path-traversal"])
def test_completed_unsafe_manifest_path_is_rejected(
    completed_core4_run, unsafe_path
):
    outcome, _config = completed_core4_run
    manifest = outcome.output_root / "file_hashes.sha256"
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        original + f"{'0' * 64}  {unsafe_path}\n", encoding="utf-8"
    )
    with pytest.raises(Core4ContractError, match="unsafe path"):
        validate_core4_artifact_contract(outcome.output_root)


def test_completed_manifest_rejects_undeclared_extra_file(completed_core4_run):
    outcome, _config = completed_core4_run
    (outcome.output_root / "undeclared-result.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(Core4ContractError, match="file set mismatch"):
        validate_core4_artifact_contract(outcome.output_root)


def test_analyzer_cli_rejects_completed_root_without_manifest(completed_core4_run):
    outcome, _config = completed_core4_run
    (outcome.output_root / "file_hashes.sha256").unlink()
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/analyze_v9_3_core4.py"),
            str(outcome.output_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "file_hashes.sha256 is missing" in completed.stderr


def test_resume_rejects_completed_root_without_manifest(completed_core4_run):
    outcome, config = completed_core4_run
    (outcome.output_root / "file_hashes.sha256").unlink()
    with pytest.raises(ConfigError, match="file_hashes.sha256 is missing"):
        Core4SensitivityRunner(config)._initialize(True)


def test_fresh_unfinished_root_initializes_without_manifest(tmp_path):
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "fresh")
    runner = Core4SensitivityRunner(config)
    runner._initialize(False)
    checkpoint = json.loads(
        (runner.root / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["phase"] == "INITIALIZED"
    assert not (runner.root / "file_hashes.sha256").exists()
    runner._initialize(True)


def test_success_writes_and_validates_manifest_before_completed_checkpoint(
    monkeypatch, tmp_path
):
    fake_engine = _install_fake_pipeline(monkeypatch, tmp_path)
    config = load_config(ROOT / "configs/v9_3_core4_smoke.yaml", expected_core="CORE-4")
    config["execution"]["output_root"] = str(tmp_path / "run")
    config["execution"]["taskset_store"] = str(tmp_path / "store")
    events = []
    original_checkpoint = Core4SensitivityRunner._write_checkpoint
    original_write_manifest = sensitivity_module.write_core4_hash_manifest
    original_validate_manifest = sensitivity_module.validate_core4_hash_manifest

    def record_checkpoint(self, **kwargs):
        events.append(f"checkpoint:{kwargs['phase']}")
        return original_checkpoint(self, **kwargs)

    def record_write_manifest(root):
        events.append("manifest:write")
        return original_write_manifest(root)

    def record_validate_manifest(root, *, require_completed_files):
        events.append(f"manifest:validate:{require_completed_files}")
        return original_validate_manifest(
            root, require_completed_files=require_completed_files
        )

    monkeypatch.setattr(Core4SensitivityRunner, "_write_checkpoint", record_checkpoint)
    monkeypatch.setattr(
        sensitivity_module, "write_core4_hash_manifest", record_write_manifest
    )
    monkeypatch.setattr(
        sensitivity_module, "validate_core4_hash_manifest", record_validate_manifest
    )
    outcome = Core4SensitivityRunner(config).run()
    assert len(fake_engine.calls) == 7
    assert outcome.stopped is False
    assert events[-4:] == [
        "checkpoint:FINALIZING",
        "manifest:write",
        "manifest:validate:True",
        "checkpoint:COMPLETED",
    ]
