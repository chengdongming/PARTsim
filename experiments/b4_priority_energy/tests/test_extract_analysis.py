import copy
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
import yaml


B4_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B4_DIR))

import analysis_common as analysis
import extract_analysis
import integration_smoke_common as smoke
import manifest_common as manifest


def _sha(material):
    return hashlib.sha256(material).hexdigest()


def _json_bytes(value, *, allow_nan=False):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=allow_nan,
        )
        + "\n"
    ).encode("utf-8")


def _task(rank):
    completed = rank + 1
    response_max = rank + 2
    return {
        "task_name": f"task_{rank}",
        "priority_rank": rank,
        "is_top4": rank < 4,
        "is_bottom6": rank >= 4,
        "released_jobs": completed,
        "completed_jobs": completed,
        "terminated_jobs": 0,
        "deadline_miss_jobs": 0,
        "unfinished_at_horizon_jobs": 0,
        "executed_core_ticks": completed,
        "completed_response_time_count": completed,
        "completed_response_time_sum_ms": completed * response_max,
        "completed_response_time_max_ms": response_max,
    }


def _result(case_id, algorithm, semantic_hash, mutation=None):
    tasks = [_task(rank) for rank in range(10)]
    if mutation is not None:
        mutation(tasks)
    scheduler = manifest.PROTOCOL["algorithm_cli_mapping"][algorithm]
    return {
        "events": [{"event_type": "ignored_by_analysis"}],
        "trace_schema_version": 3,
        "run_count": 1,
        "target_run_generation": 1,
        "run_generation": 1,
        "run_id": case_id,
        "taskset_semantic_hash": semantic_hash,
        "configured_scheduler": scheduler,
        "scheduler_display_name": algorithm,
        "scheduler_implementation": f"Fake{algorithm.replace('-', '')}",
        "expected_simulation_horizon_ms": 30000,
        "observed_simulation_end_ms": 30000,
        "simulation_completed": True,
        "observability_summary_contract_version": 1,
        "observability_summary_horizon_ms": 30000,
        "mechanism_summary": {
            "bypass_opportunity_ticks": 7,
            "actual_bypass_ticks": 5,
            "low_priority_bypass_core_ticks": 6,
            "hp_dispatch_demand_ticks": 11,
            "hp_energy_blocked_ticks": 3,
            "hp_energy_blocked_job_ticks": 4,
            "observed_decision_ticks": 30000,
        },
        "energy_summary": {
            "offered_energy_j": 0.5,
            "credited_energy_j": 0.5,
            "clipped_energy_j": 0.0,
            "consumed_energy_j": 0.25,
            "battery_min_j": 0.5,
            "battery_max_j": 0.75,
            "battery_final_j": 0.75,
            "battery_empty_ticks": 0,
            "battery_full_ticks": 0,
            "observed_energy_intervals": 30000,
        },
        "per_task_summary": tasks,
        "simulation_completion_reason": "reached_horizon",
    }


def _source():
    return {
        "E0_j": 0.5,
        "Emax_j": 1.0,
        "campaign_started": False,
        "lambda_E": "0.85",
        "not_for_paper": True,
        "phase": "integration_smoke",
        "profile_id": "b4_pe_three_stage_v1",
        "rho_E": "2",
        "schema": "test-source-v1",
        "source": {"kind": "scaled_piecewise", "scale_w": 1.0, "segments": []},
    }


def _taskset():
    return {
        "metadata": {
            "target_total_utilization": 1.2,
            "target_normalized_utilization": 0.3,
            "M": 4,
            "num_tasks": 10,
        },
        "taskset": [
            {"name": f"task_{rank}", "iat": 100 + rank}
            for rank in range(10)
        ],
    }


def _smoke_record(case_root, case_id, algorithm, semantic_hash, taskset_sha, source_sha):
    protocol = smoke.PROTOCOL
    simulator = "/tmp/fake-b4-pe-rtsim"
    system_path = "integration-smoke/artifacts/system.yml"
    taskset_path = "snapshots/taskset.yml"
    source_path = "snapshots/source.json"
    result_path = f"integration-smoke/results/{case_id}.json"
    record = {
        "schema_version": protocol["schema_version"],
        "record_type": "integration_smoke",
        "phase": "integration_smoke",
        "execution_scope": "single-real-case",
        "selected_case_count": 1,
        "campaign_started": False,
        "campaign_result_count": 0,
        "not_for_paper": True,
        "case_id": case_id,
        "algorithm": algorithm,
        "trace_schema_version": 3,
        "observability_summary_contract_version": 1,
        "observability_summary_horizon_ms": 30000,
        "observability_contract_ref": protocol["observability_contract_ref"],
        "observability_contract_sha256": protocol[
            "observability_contract_sha256"
        ],
        "candidate_v1_ref": protocol["candidate_v1_ref"],
        "candidate_v1_sha256": protocol["candidate_v1_sha256"],
        "result_audit_policy": protocol["result_audit_policy"],
        "command_argv": [
            simulator,
            system_path,
            taskset_path,
            "30000",
            "-t",
            result_path,
            "--run-id",
            case_id,
            "--taskset-semantic-hash",
            semantic_hash,
            "--b4-observability-summary",
            "--b4-summary-horizon",
            "30000",
        ],
        "simulator_path": simulator,
        "output_root": str(case_root),
        "system_config_path": system_path,
        "taskset_path": taskset_path,
        "source_artifact_path": source_path,
        "result_relpath": result_path,
        "timeout_seconds": 300,
        "retry_policy": {
            "initial_timeout_seconds": 300,
            "max_attempts": 1,
            "on_final_failure": "fail_closed",
            "retry_on": ["timeout"],
            "retry_timeout_seconds": 600,
        },
        "provenance": {
            "generator_path": "/tmp/fake-generator.py",
            "generator_sha256": "1" * 64,
            "generator_argv": ["/usr/bin/python3", "/tmp/fake-generator.py"],
            "taskset_raw_sha256": taskset_sha,
            "taskset_semantic_hash": semantic_hash,
            "system_config_sha256": _sha(algorithm.encode("utf-8")),
            "source_artifact_sha256": source_sha,
            "simulator_sha256": "2" * 64,
        },
    }
    assert set(record) == set(protocol["record_fields"])
    return record


def build_fixture(tmp_path, groups=1, algorithms=None, mutation=None):
    algorithms = list(algorithms or analysis.ALGORITHMS)
    output_root = tmp_path / "executed"
    output_root.mkdir()
    records = []
    audit_cases = []
    for group in range(groups):
        semantic_hash = _sha(f"semantic-{group}".encode("utf-8"))
        taskset = _taskset()
        taskset_bytes = yaml.safe_dump(taskset, sort_keys=False).encode("utf-8")
        taskset_sha = _sha(taskset_bytes)
        source_bytes = _json_bytes(_source())
        source_sha = _sha(source_bytes)
        for position, algorithm in enumerate(algorithms):
            slug = algorithm.lower().replace("-", "-")
            case_id = f"smoke-i5c-g{group}-p{position}-{slug}"
            case_root = (output_root / f"group-{group}" / f"case-{position}").resolve()
            (case_root / "snapshots").mkdir(parents=True)
            result_dir = case_root / "integration-smoke" / "results"
            result_dir.mkdir(parents=True)
            state_dir = case_root / ".b4pe" / "state"
            state_dir.mkdir(parents=True)
            taskset_path = case_root / "snapshots" / "taskset.yml"
            source_path = case_root / "snapshots" / "source.json"
            taskset_path.write_bytes(taskset_bytes)
            source_path.write_bytes(source_bytes)
            result = _result(case_id, algorithm, semantic_hash, mutation)
            result_path = result_dir / f"{case_id}.json"
            result_bytes = _json_bytes(result)
            result_path.write_bytes(result_bytes)
            state = {
                "case_id": case_id,
                "current_status": "succeeded",
                "algorithm": algorithm,
                "result_relpath": f"integration-smoke/results/{case_id}.json",
                "final_result_sha256": _sha(result_bytes),
                "taskset_snapshot_relpath": "snapshots/taskset.yml",
                "taskset_snapshot_sha256": taskset_sha,
                "source_snapshot_relpath": "snapshots/source.json",
                "source_snapshot_sha256": source_sha,
            }
            state_path = state_dir / f"{case_id}.json"
            state_path.write_bytes(_json_bytes(state))
            record = _smoke_record(
                case_root,
                case_id,
                algorithm,
                semantic_hash,
                taskset_sha,
                source_sha,
            )
            records.append(record)
            all_deadline = sum(
                task["deadline_miss_jobs"] for task in result["per_task_summary"]
            )
            all_terminated = sum(
                task["terminated_jobs"] for task in result["per_task_summary"]
            )
            all_unfinished = sum(
                task["unfinished_at_horizon_jobs"]
                for task in result["per_task_summary"]
            )
            issues = []
            for code, count in (
                ("deadline_miss", all_deadline),
                ("terminated", all_terminated),
                ("unfinished_at_horizon", all_unfinished),
            ):
                if count:
                    issues.append(
                        {
                            "classification": "scheduling_outcome",
                            "code": code,
                            "detail": str(count),
                        }
                    )
            audit_cases.append(
                {
                    "case_id": case_id,
                    "state_path": str(state_path.resolve()),
                    "output_root": str(case_root),
                    "algorithm": algorithm,
                    "status": "succeeded",
                    "result_relpath": state["result_relpath"],
                    "scheduler": result["configured_scheduler"],
                    "pairing": {
                        "group_id": semantic_hash,
                        "algorithm": algorithm,
                        "semantic_hash": semantic_hash,
                        "E0_j": 0.5,
                        "Emax_j": 1.0,
                        "alpha_w": 1.0,
                        "source_profile": "b4_pe_three_stage_v1",
                        "processors": 4,
                        "generator_sha256": "1" * 64,
                        "simulator_binary_sha256": "2" * 64,
                        "normalized_system": f"normalized-system-{group}",
                    },
                    "issues": issues,
                    "classifications": ["scheduling_outcome"] if issues else [],
                }
            )
    records_path = tmp_path / "records.jsonl"
    records_path.write_bytes(b"".join(_json_bytes(record) for record in records))
    audit = {
        "schema_version": 1,
        "strict": True,
        "overall_pass": True,
        "case_count": len(audit_cases),
        "infrastructure_failure_count": 0,
        "audit_failure_count": 0,
        "scheduling_outcome_count": sum(bool(case["issues"]) for case in audit_cases),
        "per_case": audit_cases,
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_bytes(_json_bytes(audit))
    return {
        "output_root": output_root,
        "records_path": records_path,
        "audit_path": audit_path,
        "records": records,
        "audit": audit,
    }


def extract(fixture):
    return analysis.build_outputs(
        fixture["output_root"],
        fixture["records_path"],
        fixture["audit_path"],
        True,
    )


def rewrite_audit(fixture):
    fixture["audit_path"].write_bytes(_json_bytes(fixture["audit"]))


def rewrite_result(fixture, case_index, result, *, allow_nan=False):
    case = fixture["audit"]["per_case"][case_index]
    result_path = Path(case["output_root"]) / case["result_relpath"]
    material = _json_bytes(result, allow_nan=allow_nan)
    result_path.write_bytes(material)
    state_path = Path(case["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["final_result_sha256"] = _sha(material)
    state_path.write_bytes(_json_bytes(state))


def load_result(fixture, case_index=0):
    case = fixture["audit"]["per_case"][case_index]
    return json.loads(
        (Path(case["output_root"]) / case["result_relpath"]).read_text(
            encoding="utf-8"
        )
    )


def test_valid_one_nine_algorithm_pairing_group(tmp_path):
    outputs, manifest_result, audit = extract(build_fixture(tmp_path))
    assert manifest_result["case_row_count"] == 9
    assert manifest_result["task_row_count"] == 90
    assert manifest_result["pairing_group_count"] == 1
    assert audit["overall_pass"] is True
    assert set(outputs) == set(analysis.OUTPUT_NAMES)
    cases = [json.loads(line) for line in outputs["cases.jsonl"].splitlines()]
    assert len({row["pairing_key"] for row in cases}) == 1
    assert [row["algorithm_order"] for row in cases] == list(range(9))


def test_valid_two_pairing_groups(tmp_path):
    _, manifest_result, _ = extract(build_fixture(tmp_path, groups=2))
    assert manifest_result["case_row_count"] == 18
    assert manifest_result["task_row_count"] == 180
    assert manifest_result["pairing_group_count"] == 2


def test_missing_algorithm_is_rejected(tmp_path):
    fixture = build_fixture(tmp_path, algorithms=analysis.ALGORITHMS[:-1])
    with pytest.raises(analysis.AnalysisError, match="pairing group size"):
        extract(fixture)


def test_duplicate_algorithm_is_rejected(tmp_path):
    algorithms = list(analysis.ALGORITHMS)
    algorithms[-1] = algorithms[0]
    fixture = build_fixture(tmp_path, algorithms=algorithms)
    with pytest.raises(analysis.AnalysisError, match="algorithm order coverage"):
        extract(fixture)


def test_duplicate_expected_case_is_rejected(tmp_path):
    fixture = build_fixture(tmp_path)
    material = fixture["records_path"].read_bytes().splitlines(keepends=True)
    fixture["records_path"].write_bytes(b"".join(material + [material[0]]))
    with pytest.raises(analysis.AnalysisError, match="duplicate expected case"):
        extract(fixture)


def test_missing_task_is_rejected(tmp_path):
    fixture = build_fixture(tmp_path)
    result = load_result(fixture)
    result["per_task_summary"].pop()
    rewrite_result(fixture, 0, result)
    with pytest.raises(analysis.AnalysisError, match="task count"):
        extract(fixture)


@pytest.mark.parametrize("rank_value", [0, 12])
def test_duplicate_or_non_contiguous_rank_is_rejected(tmp_path, rank_value):
    fixture = build_fixture(tmp_path)
    result = load_result(fixture)
    result["per_task_summary"][1]["priority_rank"] = rank_value
    rewrite_result(fixture, 0, result)
    with pytest.raises(analysis.AnalysisError, match="rank"):
        extract(fixture)


def test_schema2_formal_is_rejected():
    with pytest.raises(analysis.AnalysisError, match="formal phase requires schema3"):
        analysis.validate_trace_admission({"phase": "formal_main"}, 2)


def test_schema2_not_for_paper_smoke_is_admitted():
    assert analysis.validate_trace_admission(
        {"phase": "integration_smoke", "not_for_paper": True}, 2
    ) == "schema2_compatibility"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("overall_pass", False, "overall_pass"),
        ("infrastructure_failure_count", 1, "infrastructure failures"),
        ("audit_failure_count", 1, "audit failures"),
    ],
)
def test_failed_input_audit_is_rejected(tmp_path, field, value, message):
    fixture = build_fixture(tmp_path)
    fixture["audit"][field] = value
    rewrite_audit(fixture)
    with pytest.raises(analysis.AnalysisError, match=message):
        extract(fixture)


def test_audit_records_and_state_case_sets_must_match(tmp_path):
    fixture = build_fixture(tmp_path)
    fixture["audit"]["per_case"][0]["case_id"] = "smoke-i5c-unknown-case"
    rewrite_audit(fixture)
    with pytest.raises(analysis.AnalysisError, match="expected/audit case set"):
        extract(fixture)


def test_unknown_result_file_is_rejected(tmp_path):
    fixture = build_fixture(tmp_path)
    extra = fixture["output_root"] / "extra" / "results" / "unknown.json"
    extra.parent.mkdir(parents=True)
    extra.write_text("{}\n", encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="expected/result case set"):
        extract(fixture)


def test_task_pass_boundaries():
    valid = _task(0)
    assert analysis.task_pass(valid) is True
    deadline = dict(valid, deadline_miss_jobs=1)
    assert deadline["completed_jobs"] == deadline["released_jobs"]
    assert analysis.task_pass(deadline) is False
    terminated = dict(valid, completed_jobs=0, terminated_jobs=1)
    assert analysis.task_pass(terminated) is False
    unfinished = dict(valid, completed_jobs=0, unfinished_at_horizon_jobs=1)
    assert analysis.task_pass(unfinished) is False


@pytest.mark.parametrize(
    ("rank", "mutation", "expected"),
    [
        (0, "deadline", (False, False, True)),
        (4, "terminated", (False, True, False)),
        (9, "unfinished", (False, True, False)),
    ],
)
def test_whole_hp_lp_pass_and_scheduling_outcome(
    tmp_path, rank, mutation, expected
):
    def mutate(tasks):
        task = tasks[rank]
        if mutation == "deadline":
            task["deadline_miss_jobs"] = 1
        elif mutation == "terminated":
            task["completed_jobs"] = 0
            task["completed_response_time_count"] = 0
            task["completed_response_time_sum_ms"] = 0
            task["completed_response_time_max_ms"] = 0
            task["terminated_jobs"] = task["released_jobs"]
        else:
            task["completed_jobs"] = 0
            task["completed_response_time_count"] = 0
            task["completed_response_time_sum_ms"] = 0
            task["completed_response_time_max_ms"] = 0
            task["unfinished_at_horizon_jobs"] = task["released_jobs"]

    fixture = build_fixture(tmp_path, mutation=mutate)
    outputs, _, audit = extract(fixture)
    row = json.loads(outputs["cases.jsonl"].splitlines()[0])
    assert (row["whole_pass"], row["hp_pass"], row["lp_pass"]) == expected
    assert row["scheduling_outcomes"]["audit_issues"]
    assert audit["overall_pass"] is True
    assert audit["scheduling_outcome_case_count"] == 9


def test_top4_bottom6_and_response_time_aggregation(tmp_path):
    outputs, _, _ = extract(build_fixture(tmp_path))
    case = json.loads(outputs["cases.jsonl"].splitlines()[0])
    tasks = [
        json.loads(line)
        for line in outputs["tasks.jsonl"].splitlines()
        if json.loads(line)["case_id"] == case["case_id"]
    ]
    assert case["hp_released_jobs"] == sum(row["released_jobs"] for row in tasks[:4])
    assert case["lp_released_jobs"] == sum(row["released_jobs"] for row in tasks[4:])
    assert case["all_completed_response_time_count"] == sum(
        row["completed_response_time_count"] for row in tasks
    )
    assert case["all_completed_response_time_sum_ms"] == sum(
        row["completed_response_time_sum_ms"] for row in tasks
    )
    assert case["all_completed_response_time_max_ms"] == max(
        row["completed_response_time_max_ms"] for row in tasks
    )


def test_mechanism_and_energy_are_exact_copies(tmp_path):
    fixture = build_fixture(tmp_path)
    result = load_result(fixture)
    outputs, _, _ = extract(fixture)
    case = json.loads(outputs["cases.jsonl"].splitlines()[0])
    assert {name: case[name] for name in analysis.MECHANISM_FIELDS} == result[
        "mechanism_summary"
    ]
    assert {name: case[name] for name in analysis.ENERGY_FIELDS} == result[
        "energy_summary"
    ]


def test_csv_jsonl_parity_and_fixed_columns(tmp_path):
    outputs, _, _ = extract(build_fixture(tmp_path))
    for stem, fields in (("cases", analysis.CASE_FIELDS), ("tasks", analysis.TASK_FIELDS)):
        json_rows = [json.loads(line) for line in outputs[f"{stem}.jsonl"].splitlines()]
        csv_rows = list(
            csv.reader(io.StringIO(outputs[f"{stem}.csv"].decode("utf-8")))
        )
        assert tuple(csv_rows[0]) == fields
        assert len(csv_rows) - 1 == len(json_rows)
        assert all(tuple(row) == fields for row in json_rows)
    case = json.loads(outputs["cases.jsonl"].splitlines()[0])
    case_csv = next(csv.DictReader(io.StringIO(outputs["cases.csv"].decode())))
    assert case_csv["offered_energy_j"] == format(case["offered_energy_j"], ".17g")


def test_repeated_execution_is_byte_identical(tmp_path):
    fixture = build_fixture(tmp_path)
    first, _, _ = extract(fixture)
    second, _, _ = extract(fixture)
    assert first == second
    root_one = tmp_path / "analysis-one"
    root_two = tmp_path / "analysis-two"
    analysis.publish_outputs(root_one, first)
    analysis.publish_outputs(root_two, second)
    assert {
        name: _sha((root_one / name).read_bytes()) for name in analysis.OUTPUT_NAMES
    } == {
        name: _sha((root_two / name).read_bytes()) for name in analysis.OUTPUT_NAMES
    }


def test_manifest_has_no_absolute_paths_timestamp_or_self_hash(tmp_path):
    outputs, manifest_result, _ = extract(build_fixture(tmp_path))
    material = outputs["analysis_manifest.json"].decode("utf-8")
    assert "/tmp/" not in material
    assert "/home/" not in material
    assert "timestamp" not in material.lower()
    assert "analysis_manifest.json" not in manifest_result["output_file_sha256"]
    assert manifest_result["source_base_commit"]
    assert manifest_result["extractor_version_sha256"]


def test_analysis_root_inside_repository_is_rejected():
    with pytest.raises(analysis.AnalysisError, match="outside repository"):
        analysis.validate_analysis_root(B4_DIR / "forbidden-analysis-output")


def test_taskset_rm_rank_disagreement_is_rejected(tmp_path):
    fixture = build_fixture(tmp_path)
    case = fixture["audit"]["per_case"][0]
    state_path = Path(case["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    taskset_path = Path(case["output_root"]) / state["taskset_snapshot_relpath"]
    taskset = yaml.safe_load(taskset_path.read_text(encoding="utf-8"))
    taskset["taskset"][0]["iat"], taskset["taskset"][1]["iat"] = 101, 100
    material = yaml.safe_dump(taskset, sort_keys=False).encode("utf-8")
    taskset_path.write_bytes(material)
    state["taskset_snapshot_sha256"] = _sha(material)
    state_path.write_bytes(_json_bytes(state))
    with pytest.raises(analysis.AnalysisError, match="RM order"):
        extract(fixture)


@pytest.mark.parametrize("mutation", ["unknown", "missing"])
def test_unknown_or_missing_summary_field_is_rejected(tmp_path, mutation):
    fixture = build_fixture(tmp_path)
    result = load_result(fixture)
    if mutation == "unknown":
        result["mechanism_summary"]["unknown"] = 0
    else:
        result["energy_summary"].pop("offered_energy_j")
    rewrite_result(fixture, 0, result)
    with pytest.raises(analysis.AnalysisError, match="fields mismatch"):
        extract(fixture)


def test_nan_and_infinity_are_rejected(tmp_path):
    fixture = build_fixture(tmp_path)
    result = load_result(fixture)
    result["energy_summary"]["offered_energy_j"] = float("nan")
    rewrite_result(fixture, 0, result, allow_nan=True)
    with pytest.raises(analysis.AnalysisError, match="non-finite"):
        extract(fixture)


def test_failure_publishes_only_failed_audit_and_no_manifest(tmp_path):
    fixture = build_fixture(tmp_path)
    fixture["audit"]["overall_pass"] = False
    rewrite_audit(fixture)
    root = tmp_path / "failed-analysis"
    with pytest.raises(analysis.AnalysisError):
        analysis.build_outputs(
            fixture["output_root"],
            fixture["records_path"],
            fixture["audit_path"],
            True,
        )
    analysis.write_failure_audit(root)
    assert not (root / "analysis_manifest.json").exists()
    failure = json.loads((root / "analysis_audit.json").read_text(encoding="utf-8"))
    assert failure["overall_pass"] is False


def test_cli_gate_failure_is_nonzero_and_has_no_success_manifest(tmp_path, capsys):
    fixture = build_fixture(tmp_path)
    root = tmp_path / "cli-failure"
    status = extract_analysis.main(
        [
            "--output-root",
            str(fixture["output_root"]),
            "--expected-records",
            str(fixture["records_path"]),
            "--audit-report",
            str(fixture["audit_path"]),
            "--analysis-root",
            str(root),
        ]
    )
    assert status != 0
    assert "--strict is required" in capsys.readouterr().err
    assert not (root / "analysis_manifest.json").exists()
    failure = json.loads((root / "analysis_audit.json").read_text(encoding="utf-8"))
    assert failure["overall_pass"] is False
