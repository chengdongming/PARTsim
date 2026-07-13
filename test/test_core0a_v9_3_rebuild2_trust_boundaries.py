import copy
import hashlib
import json

import pytest

from core0a_v9_3_evidence import produce_event_order, produce_joint_cases, produce_search
from core0a_v9_3_evidence_schema import LINEAGE_REQUIRED_CHECK_TYPES
from core0a_v9_3_independent_aggregator import (
    audit_event_order,
    audit_joint,
    audit_lineage,
    audit_mutations,
    audit_search,
)
from core0a_v9_3_package_validator import validate_rebuild2_metadata


BUILD = "a" * 64


def search_evidence():
    return produce_search(BUILD)


def event_evidence():
    return produce_event_order(BUILD)


def joint_tables():
    names = (
        "joint_certification_cases.jsonl", "joint_certification_task_inputs.csv",
        "joint_certification_solver_script.csv", "joint_certification_actual_tasks.csv",
        "joint_certification_expected_tasks.csv", "joint_certification_assertions.csv",
        "joint_certification_results.csv",
    )
    return dict(zip(names, produce_joint_cases(BUILD)))


def test_actual_event_mutation_is_detected():
    cases, ticks, assertions = event_evidence()
    assertions[0]["actual_event"] = "{}"
    assert audit_event_order(cases, ticks, assertions)[0] > 0


def test_assertion_passed_true_does_not_change_independent_event_conclusion():
    cases, ticks, assertions = event_evidence()
    assertions[0]["actual_event"] = "{}"
    assertions[0]["assertion_passed"] = "true"
    assert audit_event_order(cases, ticks, assertions)[0] > 0


@pytest.mark.parametrize("mutation", ["selected", "energy", "delete_tick", "harvest_order"])
def test_event_tick_mutations_are_detected(mutation):
    cases, ticks, assertions = event_evidence()
    if mutation == "selected":
        ticks[0]["selected_jobs_json"] = "[\"tampered\"]"
    elif mutation == "energy":
        ticks[0]["energy_after"] = "999"
    elif mutation == "delete_tick":
        target = next(i for i, row in enumerate(ticks) if row["microcase_id"] == "completion-before-current-release" and row["tick"] == 1)
        ticks.pop(target)
    else:
        cases[0]["service_curve_json"] = "[999]"
    assert audit_event_order(cases, ticks, assertions)[0] > 0


def test_opaque_callback_evidence_is_rejected():
    specs, lookup, traces = search_evidence()
    specs[0]["case_kind"] = "PYTHON_LAMBDA_CALLBACK"
    assert audit_search(specs, lookup, traces)["N_search_order_violations"] > 0


def test_callback_specification_missing_lookup_row_is_rejected():
    specs, lookup, traces = search_evidence()
    lookup.pop(0)
    assert audit_search(specs, lookup, traces)["N_search_order_violations"] > 0


def test_search_trace_deleted_q_row_is_rejected():
    specs, lookup, traces = search_evidence()
    traces.pop(next(i for i, row in enumerate(traces) if row["q"] != ""))
    assert audit_search(specs, lookup, traces)["N_search_order_violations"] > 0


@pytest.mark.parametrize("mutation", ["duplicate_h", "sequence", "lookup", "closure", "restart_q2"])
def test_search_control_flow_mutations_are_rejected(mutation):
    specs, lookup, traces = search_evidence()
    if mutation == "duplicate_h":
        traces.insert(1, copy.deepcopy(traces[0]))
    elif mutation == "sequence":
        traces[0]["sequence_number"], traces[1]["sequence_number"] = traces[1]["sequence_number"], traces[0]["sequence_number"]
    elif mutation == "lookup":
        lookup[0]["envelope_value"] = "999"
    elif mutation == "closure":
        row = next(row for row in specs if row["closure_point_json"] != "null")
        row["closure_point_json"] = "null"
    else:
        row = next(row for row in traces if row["h"] not in {"", 0, "0"} and str(row["q"]) == "1")
        row["q"] = 2
    assert audit_search(specs, lookup, traces)["N_search_order_violations"] > 0


def test_joint_case_missing_task_input_is_rejected():
    tables = joint_tables()
    tables["joint_certification_task_inputs.csv"].pop(0)
    assert audit_joint(tables)[0] > 0


def test_joint_reported_pass_true_cannot_hide_actual_state_error():
    tables = joint_tables()
    tables["joint_certification_cases.jsonl"][0]["reported_passed"] = "true"
    tables["joint_certification_actual_tasks.csv"][0]["certification_status"] = "PROVISIONAL_NOT_CERTIFIED"
    assert audit_joint(tables)[0] > 0


@pytest.mark.parametrize("mutation", ["provisional", "source_candidate", "source_vector", "timeout", "suffix", "pre_finalizer", "source_after_hash"])
def test_joint_raw_state_mutations_are_rejected(mutation):
    tables = joint_tables()
    if mutation == "provisional":
        row = next(r for r in tables["joint_certification_actual_tasks.csv"] if r["certification_status"] == "PROVISIONAL_NOT_CERTIFIED")
        row["certification_status"] = "CERTIFIED"
    elif mutation in {"source_candidate", "source_vector"}:
        case = next(r["case_id"] for r in tables["joint_certification_cases.jsonl"] if r["case_kind"] == "LOC_CANDIDATE_GT_SOURCE")
        row = next(r for r in tables["joint_certification_task_inputs.csv"] if r["case_id"] == case)
        row["source_candidate" if mutation == "source_candidate" else "fixed_carry_in"] = ""
    elif mutation == "timeout":
        row = next(r for r in tables["joint_certification_solver_script.csv"] if r["solver_outcome"] == "TIMEOUT")
        row["solver_outcome"] = "NO_CANDIDATE"
    elif mutation == "suffix":
        row = next(r for r in tables["joint_certification_actual_tasks.csv"] if r["solver_status"] == "NOT_EVALUATED_AFTER_PREFIX_FAILURE")
        tables["joint_certification_actual_tasks.csv"].remove(row)
    elif mutation == "pre_finalizer":
        row = next(r for r in tables["joint_certification_results.csv"] if r["actual_taskset_proven"] == "true")
        row["pre_finalizer_status"] = "[]"
    else:
        row = next(r for r in tables["joint_certification_results.csv"] if r["source_hash_before"])
        row["source_hash_after"] = "0" * 64
    assert audit_joint(tables)[0] > 0


def lineage_tables():
    rows = []
    for index, kind in enumerate(LINEAGE_REQUIRED_CHECK_TYPES):
        expected = "NOT_RUN_DEPENDENCY" if kind == "GENERATION_FAILURE_PROPAGATION" else "ok"
        actual = "false" if kind == "DEPENDENCY_DAG_ACYCLIC" else expected
        rows.append({"check_type": kind, "violation": "false", "source_row_id": "t", "expected": expected, "actual": actual})
    return {
        "lineage_checks.csv": rows,
        "dominance_tasksets.csv": [{"source_analysis_id": "source", "local_analysis_id": "local"}],
        "finite_state_tasksets.csv": [{"taskset_id": "t", "taskset_proven": "true", "analysis_certification_status": "CERTIFIED_TASKSET"}],
    }


def test_lineage_missing_dag_check_is_rejected():
    tables = lineage_tables()
    tables["lineage_checks.csv"] = [r for r in tables["lineage_checks.csv"] if r["check_type"] != "DEPENDENCY_DAG_ACYCLIC"]
    assert audit_lineage(tables)[0] > 0


def test_real_dependency_cycle_is_rejected():
    tables = lineage_tables()
    tables["dominance_tasksets.csv"].append({"source_analysis_id": "local", "local_analysis_id": "source"})
    assert audit_lineage(tables)[0] > 0


def test_state_transition_vacuity_is_rejected():
    tables = lineage_tables()
    tables["lineage_checks.csv"] = [r for r in tables["lineage_checks.csv"] if r["check_type"] != "EXECUTION_STATE_VALID"]
    assert audit_lineage(tables)[0] > 0


def test_generation_failure_propagation_mutation_is_rejected():
    tables = lineage_tables()
    row = next(r for r in tables["lineage_checks.csv"] if r["check_type"] == "GENERATION_FAILURE_PROPAGATION")
    row["actual"] = "FINISHED"
    assert audit_lineage(tables)[0] > 0


def mutation_rows(tmp_path):
    stdout = b"target assertion\n"
    stderr = b""
    (tmp_path / "stdout.txt").write_bytes(stdout)
    (tmp_path / "stderr.txt").write_bytes(stderr)
    required = [
        "delete_release_certificate", "release_energy_below_e0",
        "certified_candidate_provisional", "delete_bound_check",
        "break_certificate_job_fk",
    ] + ["semantic-{}".format(i) for i in range(10)]
    rows = []
    for mutation_id in required:
        rows.append({
            "mutation_id": mutation_id, "argv_json": "[\"python3\",\"verifier.py\"]",
            "stdout_member_path": "stdout.txt", "stderr_member_path": "stderr.txt",
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(), "exit_code": "1",
            "expected_failing_assertion_id": "target assertion",
            "observed_failing_assertion_id": "target assertion", "failure_matches_target": "true",
            "syntax_import_failure": "false", "original_source_hash": "1" * 64,
            "mutated_source_hash": "2" * 64, "restored_source_hash": "1" * 64,
            "mutation_applied": "true", "detected": "true",
        })
    return rows


def test_mutation_missing_argv_is_rejected(tmp_path):
    rows = mutation_rows(tmp_path)
    rows[0]["argv_json"] = "[]"
    assert audit_mutations(rows, tmp_path)[0] > 0


def test_mutation_missing_stdout_or_stderr_is_rejected(tmp_path):
    rows = mutation_rows(tmp_path)
    rows[0]["stdout_member_path"] = "missing.txt"
    assert audit_mutations(rows, tmp_path)[0] > 0


def test_syntax_error_failure_is_not_detected(tmp_path):
    rows = mutation_rows(tmp_path)
    rows[0]["syntax_import_failure"] = "true"
    assert audit_mutations(rows, tmp_path)[0] > 0


def test_positive_e0_certificate_deletion_mutation_is_required(tmp_path):
    rows = mutation_rows(tmp_path)
    rows = [r for r in rows if r["mutation_id"] != "delete_release_certificate"]
    assert "delete_release_certificate" in audit_mutations(rows, tmp_path)[3]


def valid_metadata(tmp_path):
    raw = {"pilot_authorized": False, "superseded_core0a_evidence": [
        {"commit": "dcb55f6a22f4d772a74f94ac7799b79cf5da8541", "zip_sha256": "d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50", "status": "INVALIDATED"},
        {"commit": "01f582b094f376a8e00640e22d0d2f25506d0e35", "zip_sha256": "a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995", "status": "INVALIDATED"},
    ]}
    (tmp_path / "raw_evidence_manifest.json").write_text(json.dumps(raw))
    determinism = {key: 0 for key in (
        "N_environments", "N_repetitions", "N_files_compared", "N_raw_files_compared",
        "N_raw_differences", "N_gate_differences", "N_manifest_differences", "N_zip_differences")}
    determinism["excluded_execution_only_fields"] = []
    (tmp_path / "determinism_report.json").write_text(json.dumps(determinism))
    pointer = tmp_path / "pointer.json"
    pointer.write_text(json.dumps({
        "implementation_commit": "a" * 40, "evidence_commit": "b" * 40,
        "zip_filename": "evidence.zip", "zip_sha256": "c" * 64,
        "runtime_manifest_sha256": "d" * 64, "raw_manifest_sha256": "e" * 64,
        "status": "READY_FOR_THIRD_INDEPENDENT_AUDIT", "generated_from_identity": "f" * 64,
    }))
    return pointer


def test_authoritative_pointer_placeholder_is_rejected(tmp_path):
    pointer = valid_metadata(tmp_path)
    value = json.loads(pointer.read_text())
    value["evidence_commit"] = "CURRENT_COMMIT"
    pointer.write_text(json.dumps(value))
    assert any("placeholder" in error for error in validate_rebuild2_metadata(tmp_path, pointer))


def test_invalidated_evidence_list_missing_entry_is_rejected(tmp_path):
    pointer = valid_metadata(tmp_path)
    value = json.loads((tmp_path / "raw_evidence_manifest.json").read_text())
    value["superseded_core0a_evidence"].pop()
    (tmp_path / "raw_evidence_manifest.json").write_text(json.dumps(value))
    assert validate_rebuild2_metadata(tmp_path, pointer)


def test_determinism_n_fields_missing_is_rejected(tmp_path):
    pointer = valid_metadata(tmp_path)
    value = json.loads((tmp_path / "determinism_report.json").read_text())
    del value["N_zip_differences"]
    (tmp_path / "determinism_report.json").write_text(json.dumps(value))
    assert any("determinism" in error for error in validate_rebuild2_metadata(tmp_path, pointer))
