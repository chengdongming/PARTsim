import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from asap_block_v1_3_12_schema_binding import DEFAULT_CONTRACT_ROOT, V1312SchemaBinding
from asap_block_v9_3_v1_3_12_microcases import build_microcase_package


ROOT = Path(__file__).resolve().parents[1]
RESULT_VALIDATOR = "ASAP_BLOCK_result_validator_v1_3_12.py"
ARTIFACT_VALIDATOR = "ASAP_BLOCK_artifact_validator_v1_3_12.py"


def _read_csv(root, name):
    with (root / name).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames), list(reader)


def _write_csv(root, name, header, rows):
    with (root / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _update_rows(root, name, predicate, update):
    header, rows = _read_csv(root, name)
    matches = [row for row in rows if predicate(row)]
    assert matches
    update(matches[0])
    _write_csv(root, name, header, rows)


def _refresh_integrity(root):
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in manifest["files"]:
        manifest["files"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    names = [
        line.split("  ", 1)[1]
        for line in (root / "sha256sum.txt").read_text(encoding="utf-8").splitlines()
    ]
    (root / "sha256sum.txt").write_text(
        "".join(
            "{}  {}\n".format(hashlib.sha256((root / name).read_bytes()).hexdigest(), name)
            for name in sorted(names)
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def package(tmp_path_factory):
    root = tmp_path_factory.mktemp("v93_v1312") / "package"
    archive = root.parent / "package.zip"
    summary = build_microcase_package(root, archive)
    return root, archive, summary


def test_complete_package_closes_frozen_result_and_artifact_validators(package):
    root, archive, summary = package
    result = subprocess.run(
        [sys.executable, str(root / RESULT_VALIDATOR), "--profile", "DIAGNOSTIC", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    artifact = subprocess.run(
        [sys.executable, str(DEFAULT_CONTRACT_ROOT / ARTIFACT_VALIDATOR), str(DEFAULT_CONTRACT_ROOT)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert artifact.returncode == 0, artifact.stdout + artifact.stderr
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        assert set(zf.namelist()) == {path.name for path in root.iterdir()}
    assert summary == {
        "analysis_count": 8,
        "certified_taskset_count": 5,
        "dependency_record_count": 2,
        "failure_provenance_counts": {
            "DEPENDENCY_NOT_APPLICABLE": 2,
            "NONE": 12,
            "NO_CANDIDATE": 2,
            "UPSTREAM_PREFIX_FAILURE": 1,
        },
        "file_count": 51,
    }


def test_microcase_states_and_generation_failure_execution_semantics(package):
    root, _, _ = package
    _, analyses = _read_csv(root, "per_taskset_results.csv")
    _, tasks = _read_csv(root, "per_task_results.csv")
    _, deps = _read_csv(root, "rta_dependency_records.csv")
    a = [row for row in analyses if "Microcase A" in _plan_label(root, row["request_id"])]
    assert len(a) == 5
    assert all(row["analysis_solver_status"] == "COMPLETED" for row in a)
    assert all(row["analysis_certification_status"] == "CERTIFIED_TASKSET" for row in a)
    source = next(row for row in a if row["variant"] == "CW-Theta^cw")
    target = next(row for row in a if row["variant"] == "LOC-Theta^cw")
    assert all(row["source_analysis_certification_status"] == "CERTIFIED_TASKSET" for row in deps if row["analysis_run_id"] == target["analysis_run_id"])
    source_candidates = {row["task_id"]: int(row["candidate_response_time"]) for row in tasks if row["analysis_run_id"] == source["analysis_run_id"]}
    assert all(int(row["candidate_response_time"]) <= source_candidates[row["task_id"]] for row in tasks if row["analysis_run_id"] == target["analysis_run_id"])
    b_target = next(row for row in analyses if row["variant"] == "LOC-Theta^cw" and row["analysis_certification_status"] == "NOT_APPLICABLE")
    b_tasks = [row for row in tasks if row["analysis_run_id"] == b_target["analysis_run_id"]]
    assert all(row["task_failure_reason_code"] == "DEPENDENCY_NOT_APPLICABLE" for row in b_tasks)
    c_tasks = [row for row in tasks if row["task_failure_reason_code"] in {"NO_CANDIDATE", "UPSTREAM_PREFIX_FAILURE"}]
    assert any(row["task_failure_detail"] == "closure exhausted through task deadline" for row in c_tasks)
    assert any(row["task_failure_reason_code"] == "UPSTREAM_PREFIX_FAILURE" for row in c_tasks)
    _, generations = _read_csv(root, "generation_requests.csv")
    failed = next(row for row in generations if row["generation_status"] == "GENERATION_FAILURE")
    _, plans = _read_csv(root, "run_plan_definition.csv")
    downstream = next(row for row in plans if row["request_type"] == "ANALYSIS" and row["taskset_request_id"] == failed["request_id"])
    _, logs = _read_csv(root, "run_execution_log.csv")
    assert [row["execution_status"] for row in logs if row["request_id"] == failed["request_id"]][-1] == "FINISHED"
    assert [row["execution_status"] for row in logs if row["request_id"] == downstream["request_id"]][-1] == "NOT_RUN_DEPENDENCY"


def _plan_label(root, request_id):
    _, plans = _read_csv(root, "run_plan_definition.csv")
    return next(row["human_label"] for row in plans if row["request_id"] == request_id)


def test_round_trip_seven_failure_classes_without_float(package, tmp_path):
    root, _, _ = package
    binding = V1312SchemaBinding()
    _, rows = _read_csv(root, "per_task_results.csv")
    decoded = [binding.decode_row("per_task_results.csv", row) for row in rows]
    success = next(row for row in decoded if row["task_failure_reason_code"] == "NONE")
    no_candidate = next(row for row in decoded if row["task_failure_reason_code"] == "NO_CANDIDATE")
    prefix = next(row for row in decoded if row["task_failure_reason_code"] == "UPSTREAM_PREFIX_FAILURE")
    dependency = next(row for row in decoded if row["task_failure_reason_code"] == "DEPENDENCY_NOT_APPLICABLE")

    def altered(base, status, certification, code, detail, dominance="NOT_CHECKED"):
        row = dict(base)
        row.update(
            task_solver_status=status,
            task_certification_status=certification,
            task_failure_reason_code=code,
            task_failure_detail=detail,
            dominance_invariant_status=dominance,
            candidate_response_time=None,
            closing_w=None,
            witness_h=None,
            critical_q=None,
        )
        row["task_result_hash"] = binding.task_result_hash(row)
        return row

    records = [
        success,
        no_candidate,
        altered(no_candidate, "TIMEOUT", "NOT_CERTIFIED", "SOLVER_TIMEOUT", None),
        altered(no_candidate, "NUMERIC_ERROR", "NOT_CERTIFIED", "NUMERIC_ERROR", "numeric guard rejected analysis"),
        prefix,
        dependency,
        altered(no_candidate, "NO_CANDIDATE", "NOT_CERTIFIED", "DOMINANCE_INVARIANT_VIOLATION", "local result violated frozen carry-in dominance", "DOMINANCE_INVARIANT_VIOLATION"),
    ]
    for row in records:
        encoded = binding.encode_row("per_task_results.csv", row)
        reloaded = binding.decode_row("per_task_results.csv", encoded)
        assert reloaded == row
        assert not any(isinstance(value, float) for value in reloaded.values())


def test_deterministic_rerun_has_zero_file_differences(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    zip1, zip2 = tmp_path / "first.zip", tmp_path / "second.zip"
    build_microcase_package(first, zip1)
    build_microcase_package(second, zip2)
    names = sorted(path.name for path in first.iterdir())
    assert names == sorted(path.name for path in second.iterdir())
    assert all((first / name).read_bytes() == (second / name).read_bytes() for name in names)
    assert zip1.read_bytes() == zip2.read_bytes()
    assert len(names) == 51


def _mutation(root, name):
    if name in {"delete_required_column", "legacy_39_columns", "delete_failure_code"}:
        table = "per_task_results.csv"
        header, rows = _read_csv(root, table)
        remove = {
            "delete_required_column": ["task_id"],
            "legacy_39_columns": ["task_failure_reason_code", "task_failure_detail"],
            "delete_failure_code": ["task_failure_reason_code"],
        }[name]
        header = [field for field in header if field not in remove]
        for row in rows:
            for field in remove:
                row.pop(field)
        _write_csv(root, table, header, rows)
        return "binding"
    updates = {
        "unknown_failure_code": ("per_task_results.csv", lambda r: True, lambda r: r.update(task_failure_reason_code="ALIEN")),
        "success_non_none": ("per_task_results.csv", lambda r: r["task_solver_status"] == "CANDIDATE_FOUND", lambda r: r.update(task_failure_reason_code="NO_CANDIDATE", task_failure_detail="closure exhausted through task deadline")),
        "failure_none": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_reason_code="NONE", task_failure_detail="")),
        "timeout_no_candidate": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_solver_status="TIMEOUT")),
        "dominance_as_no_candidate": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(dominance_invariant_status="DOMINANCE_INVARIANT_VIOLATION")),
        "absolute_path_detail": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="/tmp/raw/exception")),
        "nul_detail": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="bad\x00detail")),
        "overlong_detail": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="x" * 5000)),
        "empty_detail_as_null": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="")),
        "detail_hash_mismatch": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="numeric guard rejected analysis")),
        "theory_hash_wrong": ("per_taskset_results.csv", lambda r: True, lambda r: r.update(theory_document_sha256="0" * 64)),
        "loc_vector_hash_wrong": ("rta_dependency_records.csv", lambda r: True, lambda r: r.update(carry_in_vector_hash="0" * 64)),
        "dependency_certified": ("per_task_results.csv", lambda r: r["variant"] == "LOC-Theta^cw", lambda r: r.update(carry_in_source_certification_status="CERTIFIED")),
        "dependency_provisional": ("per_task_results.csv", lambda r: r["variant"] == "LOC-Theta^cw", lambda r: r.update(carry_in_source_certification_status="PROVISIONAL_NOT_CERTIFIED")),
        "uncertified_source_certified_target": ("per_taskset_results.csv", lambda r: r["variant"] == "CW-Theta^cw" and r["analysis_certification_status"] == "CERTIFIED_TASKSET", lambda r: r.update(analysis_certification_status="NOT_CERTIFIED")),
        "source_task_provisional": ("per_task_results.csv", lambda r: r["variant"] == "CW-Theta^cw" and r["task_certification_status"] == "CERTIFIED", lambda r: r.update(task_certification_status="PROVISIONAL_NOT_CERTIFIED")),
        "provisional_target_certified": ("per_task_results.csv", lambda r: r["task_certification_status"] == "PROVISIONAL_NOT_CERTIFIED", lambda r: r.update(task_certification_status="CERTIFIED")),
        "na_target_certified": ("per_taskset_results.csv", lambda r: r["analysis_certification_status"] == "NOT_APPLICABLE", lambda r: r.update(analysis_certification_status="CERTIFIED_TASKSET", taskset_proven="true")),
        "dangling_fk": ("tasksets.csv", lambda r: True, lambda r: r.update(materialization_request_id="missing-request")),
        "numeric_scale_mismatch": ("per_taskset_results.csv", lambda r: r["variant"] == "LOC-Theta^cw", lambda r: r.update(energy_numeric_scale="10")),
        "taskset_proven_mismatch": ("per_taskset_results.csv", lambda r: r["analysis_certification_status"] == "CERTIFIED_TASKSET", lambda r: r.update(taskset_proven="false")),
        "local_candidate_gt_source": ("per_task_results.csv", lambda r: r["variant"] == "LOC-Theta^cw" and r["task_certification_status"] == "CERTIFIED", lambda r: r.update(candidate_response_time="999", closing_w="999")),
        "failure_hash_not_recomputed": ("per_task_results.csv", lambda r: r["task_failure_reason_code"] == "NONE", lambda r: r.update(task_failure_reason_code="NO_CANDIDATE", task_failure_detail="closure exhausted through task deadline")),
        "raw_exception_detail": ("per_task_results.csv", lambda r: r["task_solver_status"] == "NO_CANDIDATE", lambda r: r.update(task_failure_detail="Traceback: RuntimeError at /home/user/private.py")),
    }
    if name == "formal_hash_wrong":
        path = root / "formal_contract.yaml"
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
        obj["contract_metadata"]["formal_contract_hash"] = "0" * 64
        path.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif name == "duplicate_pk":
        header, rows = _read_csv(root, "per_task_results.csv")
        rows.append(dict(rows[0]))
        _write_csv(root, "per_task_results.csv", header, rows)
    elif name == "generation_failure_still_runs":
        _, generations = _read_csv(root, "generation_requests.csv")
        failed = next(row for row in generations if row["generation_status"] == "GENERATION_FAILURE")
        _, plans = _read_csv(root, "run_plan_definition.csv")
        downstream = next(row for row in plans if row["request_type"] == "ANALYSIS" and row["taskset_request_id"] == failed["request_id"])
        _update_rows(root, "run_execution_log.csv", lambda r: r["request_id"] == downstream["request_id"] and r["execution_status"] == "NOT_RUN_DEPENDENCY", lambda r: r.update(execution_status="FINISHED"))
    else:
        table, predicate, update = updates[name]
        _update_rows(root, table, predicate, update)
    _refresh_integrity(root)
    return "result"


MUTATIONS = [
    "delete_required_column", "legacy_39_columns", "delete_failure_code",
    "unknown_failure_code", "success_non_none", "failure_none", "timeout_no_candidate",
    "dominance_as_no_candidate", "absolute_path_detail", "nul_detail", "overlong_detail",
    "empty_detail_as_null", "detail_hash_mismatch", "theory_hash_wrong", "formal_hash_wrong",
    "loc_vector_hash_wrong", "dependency_certified", "dependency_provisional",
    "uncertified_source_certified_target", "source_task_provisional",
    "provisional_target_certified", "na_target_certified", "duplicate_pk", "dangling_fk",
    "numeric_scale_mismatch", "taskset_proven_mismatch", "local_candidate_gt_source",
    "generation_failure_still_runs", "failure_hash_not_recomputed", "raw_exception_detail",
]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_all_thirty_negative_mutations_fail_closed(package, tmp_path, mutation):
    pristine, _, _ = package
    root = tmp_path / mutation
    shutil.copytree(pristine, root)
    validator = _mutation(root, mutation)
    if validator == "binding":
        command = [sys.executable, str(ROOT / "asap_block_v1_3_12_schema_binding.py"), str(root)]
    else:
        command = [sys.executable, str(root / RESULT_VALIDATOR), "--profile", "DIAGNOSTIC", str(root)]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    assert result.returncode != 0, mutation


def test_mutation_catalog_is_exactly_thirty():
    assert len(MUTATIONS) == len(set(MUTATIONS)) == 30
