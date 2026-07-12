#!/usr/bin/env python3
"""Independently aggregate v9.3 CORE-0A gates from row-level evidence."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

import yaml

from core0a_v9_3_evidence_schema import RAW_TABLES, SCHEMA_VERSION, TABLE_SCHEMAS
from core0a_v9_3_oracles import processor_reference


VERSION = "CORE0A-INDEPENDENT-2.0"


class AggregationError(ValueError):
    pass


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def semantic_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_bytes(value)).hexdigest()


def parse_bool(value: str, label: str) -> bool:
    if value not in {"true", "false"}:
        raise AggregationError("{} is not canonical boolean".format(label))
    return value == "true"


def parse_fraction(value: str, label: str) -> Fraction:
    try:
        return Fraction(value)
    except Exception as exc:
        raise AggregationError("{} is not an exact rational".format(label)) from exc


def load_json_text(value: str, label: str):
    try:
        return json.loads(value)
    except Exception as exc:
        raise AggregationError("{} is not canonical JSON".format(label)) from exc


def read_tables(root: Path):
    manifest_path = root / "raw_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise AggregationError("raw evidence schema version mismatch")
    if set(manifest.get("files", {})) != set(RAW_TABLES):
        raise AggregationError("raw evidence manifest file set mismatch")
    tables = {}
    build = manifest.get("build_identity_hash")
    for name in RAW_TABLES:
        path = root / name
        if file_hash(path) != manifest["files"][name]:
            raise AggregationError("raw evidence member hash mismatch: {}".format(name))
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != TABLE_SCHEMAS[name]["fields"]:
                raise AggregationError("raw evidence header mismatch: {}".format(name))
            rows = list(reader)
        if len(rows) != manifest.get("row_counts", {}).get(name):
            raise AggregationError("raw evidence row count mismatch: {}".format(name))
        pk = TABLE_SCHEMAS[name]["primary_key"]
        keys = [tuple(row[field] for field in pk) for row in rows]
        if len(keys) != len(set(keys)):
            raise AggregationError("raw evidence duplicate PK: {}".format(name))
        for index, row in enumerate(rows, 2):
            digest = row.get("input_hash", "")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise AggregationError("invalid input hash: {}:{}".format(name, index))
            if row.get("build_identity_hash") != build:
                raise AggregationError("build identity mismatch: {}:{}".format(name, index))
        tables[name] = rows
    if file_hash(root / "build_identity.json") != manifest.get("build_identity_sha256"):
        raise AggregationError("build identity file hash mismatch")
    if file_hash(root / "finite_state_domain.json") != manifest.get("finite_state_domain_sha256"):
        raise AggregationError("finite-state domain hash mismatch")
    return manifest, tables


def _task(value):
    class Task:
        pass

    task = Task()
    task.name = value["task_id"]
    task.wcet = int(value["C"])
    task.deadline = int(value["D"])
    task.period = int(value["T"])
    task.power = Fraction(value["P"])
    return task


def audit_search(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["task_case_id"]].append(row)
    violations = []
    observed_w = observed_h = observed_q = 0
    expected_w = expected_h = expected_q = 0
    duplicate_count = 0
    for case_id, events in groups.items():
        events.sort(key=lambda row: int(row["sequence_number"]))
        first = events[0]
        target = _task(load_json_text(first["task_json"], "search task"))
        hp = [_task(value) for value in load_json_text(first["hp_json"], "search hp")]
        theta = {key: int(value) for key, value in load_json_text(first["theta_json"], "search theta").items()}
        processors = int(first["M"])
        coordinates = []
        decision = {}
        for row in events:
            w = int(row["w"])
            if row["event_type"] == "W_SKIPPED_A_GT_W":
                coordinates.append((w, None, None, "SKIP"))
                continue
            h = int(row["h"])
            q = int(row["q"])
            coordinates.append((w, h, q, "Q"))
            envelope = parse_fraction(row["envelope_value"], "search envelope")
            service = parse_fraction(row["service_value"], "search service")
            expected_q_result = "PASS" if envelope <= service else "FAIL"
            if row["q_result"] != expected_q_result:
                violations.append([case_id, "q predicate", w, h, q])
            if int(row["service_index"]) != h + q - 1:
                violations.append([case_id, "service index", w, h, q])
            expected_coverage = w if first["variant"] == "complete" else q + h
            if int(row["coverage_index"]) != expected_coverage:
                violations.append([case_id, "coverage index", w, h, q])
            decision[(w, h, q)] = expected_q_result

        expected = []
        closed = False
        for w in range(target.wcet, target.deadline + 1):
            expected_w += 1
            a_value = target.wcet + processor_reference(target, hp, w, processors, theta)
            if a_value > w:
                expected.append((w, None, None, "SKIP"))
                continue
            for h in range(0, w - a_value + 1):
                expected_h += 1
                h_valid = True
                for q in range(1, a_value + 1):
                    expected_q += 1
                    expected.append((w, h, q, "Q"))
                    outcome = decision.get((w, h, q))
                    if outcome != "PASS":
                        h_valid = False
                        break
                if h_valid:
                    closed = True
                    break
            if closed:
                break
        if coordinates != expected:
            violations.append([case_id, "visit order", expected, coordinates])
        duplicate_count += len(coordinates) - len(set(coordinates))
        observed_w += len({int(row["w"]) for row in events})
        observed_h += len({(int(row["w"]), int(row["h"])) for row in events if row["h"] != ""})
        observed_q += sum(row["q"] != "" for row in events)
    return {
        "N_tasks": len(groups),
        "N_expected_w": expected_w,
        "N_observed_w": observed_w,
        "N_expected_h": expected_h,
        "N_observed_h": observed_h,
        "N_expected_q": expected_q,
        "N_observed_q": observed_q,
        "N_search_omissions": sum(item[1] == "visit order" for item in violations),
        "N_search_duplicates": duplicate_count,
        "N_search_order_violations": len(violations),
        "violations": violations[:5],
    }


def validate_fks(tables):
    failures = []

    def check(source, source_fields, target, target_fields):
        target_keys = {tuple(row[field] for field in target_fields) for row in tables[target]}
        for row in tables[source]:
            key = tuple(row[field] for field in source_fields)
            if key not in target_keys:
                failures.append([source, key, target])

    check("dominance_task_results.csv", ["taskset_hash"], "dominance_tasksets.csv", ["taskset_hash"])
    for source in ("finite_state_jobs.csv", "finite_state_ticks.csv"):
        check(source, ["taskset_id"], "finite_state_tasksets.csv", ["taskset_id"])
    for source in ("release_energy_certificates.csv", "bound_checks.csv"):
        check(source, ["taskset_id", "job_id"], "finite_state_jobs.csv", ["taskset_id", "job_id"])
    return failures


ALLOWED_AST = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
)


def eval_predicate(expression: str, counts: Mapping[str, int]) -> bool:
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST):
            raise AggregationError("unsupported predicate syntax")
        if isinstance(node, ast.Name) and node.id not in counts:
            raise AggregationError("predicate references unknown count")
    return bool(eval(compile(tree, "<gate>", "eval"), {"__builtins__": {}}, dict(counts)))


def aggregate(root: Path, template_path: Path):
    manifest, tables = read_tables(root)
    global_failures = []
    workload_mismatch = sum(not parse_bool(row["match"], "workload match") for row in tables["workload_cases.csv"])
    workload_monotonic = sum(not parse_bool(row["passed"], "workload monotonic") for row in tables["workload_monotonicity_checks.csv"])
    processor_mismatch = sum(not parse_bool(row["match"], "processor match") for row in tables["processor_cases.csv"])
    envelope_mismatch = sum(not parse_bool(row["match"], "envelope match") for row in tables["envelope_cases.csv"])
    if any((workload_mismatch, workload_monotonic, processor_mismatch, envelope_mismatch)):
        global_failures.append("independent mathematical oracle mismatch")

    search = audit_search(tables["search_trace_events.csv"])
    if search["N_search_order_violations"] or search["N_search_duplicates"]:
        global_failures.append("search visitation violation")

    service_errors = 0
    expected_valid = expected_invalid = invalid_accepted = valid_rejected = illegal_candidates = illegal_certifications = 0
    for row in tables["service_curve_cases.csv"]:
        expected = parse_bool(row["expected_valid"], "expected_valid")
        accepted = parse_bool(row["production_accepted"], "production_accepted")
        candidate = parse_bool(row["candidate_returned"], "candidate_returned")
        certified = parse_bool(row["certification_returned"], "certification_returned")
        expected_valid += expected
        expected_invalid += not expected
        invalid_accepted += (not expected) and accepted
        valid_rejected += expected and not accepted
        illegal_candidates += (not expected) and candidate
        illegal_certifications += (not expected) and certified
        service_errors += not parse_bool(row["match"], "service match")
    service_errors += invalid_accepted + valid_rejected + illegal_candidates + illegal_certifications
    if service_errors:
        global_failures.append("service-curve validation mismatch")

    event_failures = sum(not parse_bool(row["assertion_passed"], "event assertion") for row in tables["scheduler_event_order_traces.csv"])
    state_failures = sum(not parse_bool(row["passed"], "joint state") for row in tables["joint_certification_cases.csv"])
    if event_failures or state_failures:
        global_failures.append("event/state-machine failure")

    dominance_rows = tables["dominance_tasksets.csv"]
    dominance_hashes = {row["taskset_hash"] for row in dominance_rows}
    dominance_duplicates = len(dominance_rows) - len(dominance_hashes)
    dominance_violations = sum(parse_bool(row["dominance_violation"], "dominance violation") for row in tables["dominance_task_results.csv"])
    vector_mismatches = 0
    for row in dominance_rows:
        source_vector = load_json_text(row["source_vector_json"], "source vector")
        local_vector = load_json_text(row["local_frozen_vector_json"], "local vector")
        if semantic_hash("CORE0A:DOMINANCE:VECTOR", source_vector) != row["source_vector_hash"]:
            vector_mismatches += 1
        if semantic_hash("CORE0A:DOMINANCE:VECTOR", local_vector) != row["local_vector_hash"]:
            vector_mismatches += 1
        if row["source_vector_hash"] != row["local_vector_hash"]:
            vector_mismatches += 1
    if len(dominance_hashes) < 200 or dominance_duplicates or dominance_violations or vector_mismatches:
        global_failures.append("dominance uniqueness/vector invariant failure")

    finite_tasksets = tables["finite_state_tasksets.csv"]
    finite_jobs = tables["finite_state_jobs.csv"]
    finite_ticks = tables["finite_state_ticks.csv"]
    bound_checks = tables["bound_checks.csv"]
    certificates = tables["release_energy_certificates.csv"]
    fk_failures = validate_fks(tables)
    if fk_failures:
        global_failures.append("raw evidence FK failure")
    tick_counts = Counter(row["taskset_id"] for row in finite_ticks)
    for row in finite_tasksets:
        if tick_counts[row["taskset_id"]] != int(row["observation_horizon"]):
            global_failures.append("finite-state tick coverage failure")
            break
    job_keys = {(row["taskset_id"], row["job_id"]) for row in finite_jobs}
    certificate_by_key = {
        (row["taskset_id"], row["job_id"]): row for row in certificates
    }
    bound_by_key = {
        (row["taskset_id"], row["job_id"]): row for row in bound_checks
    }
    finite_structure_failures = 0
    if set(certificate_by_key) != job_keys:
        finite_structure_failures += len(set(certificate_by_key) ^ job_keys) or 1
    for key, certificate in certificate_by_key.items():
        executed = parse_bool(certificate["bound_check_executed"], "bound executed")
        if executed != (key in bound_by_key):
            finite_structure_failures += 1
        if key in bound_by_key:
            bound = bound_by_key[key]
            for certificate_field, bound_field in (
                ("release", "release_boundary"),
                ("release_energy", "release_energy"),
                ("E0", "E0"),
            ):
                if certificate[certificate_field] != bound[bound_field]:
                    finite_structure_failures += 1
            if certificate["certificate_status"] != "SATISFIED":
                finite_structure_failures += 1
    if finite_structure_failures:
        global_failures.append("finite-state certificate/bound cardinality failure")
    bound_violations = sum(parse_bool(row["violation"], "bound violation") for row in bound_checks)
    inconclusive = sum(bool(row["inconclusive_reason"]) for row in finite_tasksets) + sum(bool(row["inconclusive_reason"]) for row in bound_checks)
    internal_error = sum(parse_bool(row["internal_error"], "internal error") for row in finite_tasksets)
    positive_certificates = [
        row
        for row in certificates
        if parse_bool(row["positive_E0"], "positive E0")
        and row["certificate_status"] == "SATISFIED"
        and parse_bool(row["candidate_jointly_certified"], "jointly certified")
        and parse_bool(row["bound_check_executed"], "bound executed")
        and parse_fraction(row["release_energy"], "release energy") >= parse_fraction(row["E0"], "E0")
    ]
    positive_planned_tracks = {
        row["taskset_id"]
        for row in finite_tasksets
        if parse_fraction(row["E0"], "finite taskset E0") > 0
    }
    positive_release_rows = [
        row
        for row in certificates
        if parse_bool(row["positive_E0"], "positive E0 release row")
    ]
    if (
        bound_violations
        or inconclusive
        or internal_error
        or finite_structure_failures
        or not positive_certificates
    ):
        global_failures.append("finite-state soundness/non-vacuity failure")

    mutation_rows = tables["mutation_runs.csv"]
    mutation_failures = sum(
        not (
            parse_bool(row["mutation_applied"], "mutation applied")
            and row["original_file_hash"] != row["mutated_file_hash"]
            and int(row["test_exit_code"]) != 0
            and parse_bool(row["failure_matches_target"], "failure target")
            and row["restored_file_hash"] == row["original_file_hash"]
            and parse_bool(row["detected"], "mutation detected")
        )
        for row in mutation_rows
    )
    if len(mutation_rows) != 15 or mutation_failures:
        global_failures.append("real mutation failure")

    lineage_failures = sum(not parse_bool(row["passed"], "lineage passed") for row in tables["lineage_checks.csv"])
    if lineage_failures:
        global_failures.append("lineage check failure")

    finite_certified = sum(parse_bool(row["taskset_proven"], "taskset proven") for row in finite_tasksets)
    dominance_common = sum(parse_bool(row["joint_certified"], "joint certified") for row in dominance_rows)
    energy_blocking = sum(int(row["energy_blocking_ticks"]) for row in finite_jobs)
    processor_blocking = sum(int(row["processor_blocking_ticks"]) for row in finite_jobs)
    lineage_rows = tables["lineage_checks.csv"]
    counts = {
        "full_w_q_h_scan": {"N_tasks_checked": search["N_tasks"], "N_scan_violations": search["N_search_order_violations"]},
        "exact_exhaustive_domain_zero_mismatch": {
            "N_exhaustive_instances": len({row["case_id"] for row in tables["envelope_cases.csv"] if row["domain"] == "exhaustive"}),
            "N_mismatches": sum(not parse_bool(row["match"], "envelope match") for row in tables["envelope_cases.csv"] if row["domain"] == "exhaustive"),
        },
        "exact_random_boundary_instances_at_least_10000": {
            "N_random_boundary_instances": len({row["case_id"] for row in tables["envelope_cases.csv"] if row["domain"] == "random"}),
            "N_mismatches": sum(not parse_bool(row["match"], "envelope match") for row in tables["envelope_cases.csv"] if row["domain"] == "random"),
        },
        "processor_term_direct_scan_zero_mismatch": {"N_instances": len(tables["processor_cases.csv"]), "N_mismatches": processor_mismatch},
        "finite_state_counterexample_search": {
            "N_instances": len(finite_tasksets),
            "N_complete": sum(parse_bool(row["enumeration_complete"], "enumeration complete") for row in finite_tasksets),
            "N_inconclusive": inconclusive,
            "N_internal_error": internal_error,
            "N_certified_bound_violations": bound_violations,
        },
        "event_order_and_scheduler_semantics_microcases": {
            "N_microcases": len({row["microcase_id"] for row in tables["scheduler_event_order_traces.csv"]}),
            "N_failures": event_failures,
        },
        "joint_certification_state_machine": {"N_state_cases": len(tables["joint_certification_cases.csv"]), "N_failures": state_failures},
        "loc_theta_cw_dominance_invariant": {"N_common_cases": dominance_common, "N_violations": dominance_violations + dominance_duplicates + vector_mismatches},
        "service_curve_contract_checks": {"N_curves": len(tables["service_curve_cases.csv"]), "N_invalid": service_errors},
        "schema_lineage_and_request_state_machine": {
            "N_rows": len(lineage_rows),
            "N_fk_violations": sum(row["check_type"] == "FK_INTEGRITY" and row["passed"] != "true" for row in lineage_rows),
            "N_state_transition_violations": sum(row["check_type"] == "STATE_TRANSITION" and row["passed"] != "true" for row in lineage_rows),
            "N_cycle_violations": sum(row["check_type"] == "DAG_CYCLE" and row["passed"] != "true" for row in lineage_rows),
        },
        "non_vacuity_coverage": {
            "N_certified_tasksets": finite_certified,
            "N_energy_blocking_cases": energy_blocking,
            "N_processor_interference_cases": processor_blocking,
            "N_complete_local_common_cases": dominance_common,
            "N_positive_E0_satisfied_traces": len(positive_certificates),
        },
    }
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    gates = {}
    for gate_id, gate_counts in counts.items():
        spec = template["CORE0A_gates"][gate_id]
        expected_keys = sorted(spec.get("counts", {}))
        if sorted(gate_counts) != expected_keys:
            raise AggregationError("frozen count-key mismatch: {}".format(gate_id))
        predicate_passed = eval_predicate(spec["predicate"], gate_counts)
        gates[gate_id] = {
            "predicate": spec["predicate"],
            "counts": gate_counts,
            "status": "PASSED" if predicate_passed else "FAILED",
        }
    status = "PASSED" if not global_failures and all(gate["status"] == "PASSED" for gate in gates.values()) else "FAILED"
    return {
        "status": status,
        "aggregator_version": VERSION,
        "raw_manifest_sha256": file_hash(root / "raw_evidence_manifest.json"),
        "build_identity_hash": manifest["build_identity_hash"],
        "gates": gates,
        "metrics": {
            "workload_cases": len(tables["workload_cases.csv"]),
            "workload_mismatches": workload_mismatch,
            "workload_monotonicity_checks": len(tables["workload_monotonicity_checks.csv"]),
            "workload_monotonicity_violations": workload_monotonic,
            "processor_cases": len(tables["processor_cases.csv"]),
            "processor_mismatches": processor_mismatch,
            "envelope_rows": len(tables["envelope_cases.csv"]),
            "envelope_mismatches": envelope_mismatch,
            "search": search,
            "service_curve": {
                "N_curve_cases": len(tables["service_curve_cases.csv"]),
                "N_expected_valid": expected_valid,
                "N_expected_invalid": expected_invalid,
                "N_invalid_curves_accepted": invalid_accepted,
                "N_valid_curves_rejected": valid_rejected,
                "N_illegal_curve_candidates": illegal_candidates,
                "N_illegal_curve_certifications": illegal_certifications,
            },
            "dominance": {
                "N_requested_cases": len(dominance_rows),
                "N_unique_taskset_hashes": len(dominance_hashes),
                "N_duplicate_tasksets": dominance_duplicates,
                "N_source_cw_certified": sum(row["source_certification_status"] == "CERTIFIED_TASKSET" for row in dominance_rows),
                "N_loc_theta_cw_joint_certified": dominance_common,
                "N_candidate_comparisons": sum(parse_bool(row["candidate_compared"], "candidate compared") for row in tables["dominance_task_results.csv"]),
                "N_dominance_violations": dominance_violations,
                "N_vector_mismatches": vector_mismatches,
            },
            "finite_state": {
                "N_tasksets": len(finite_tasksets),
                "N_jobs": len(finite_jobs),
                "N_ticks": len(finite_ticks),
                "N_certificates": len(certificates),
                "N_bound_checks": len(bound_checks),
                "N_structure_failures": finite_structure_failures,
                "N_positive_E0_certified_jobs": len(positive_certificates),
                "N_positive_E0_planned_tracks": len(positive_planned_tracks),
                "N_positive_E0_release_rows": len(positive_release_rows),
                "N_positive_E0_satisfied_traces": len(positive_certificates),
                "N_processor_blocking": processor_blocking,
                "N_energy_blocking": energy_blocking,
                "N_bound_violations": bound_violations,
            },
            "mutations": {"N_runs": len(mutation_rows), "N_failures": mutation_failures},
            "lineage": {"N_checks": len(lineage_rows), "N_failures": lineage_failures, "N_fk_failures": len(fk_failures)},
        },
        "global_failures": global_failures,
    }


def compare_report(result, report_path: Path):
    report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    differences = []
    for gate_id, gate in result["gates"].items():
        reported = report["CORE0A_gates"][gate_id]
        if reported.get("counts") != gate["counts"]:
            differences.append("count mismatch:{}".format(gate_id))
        if reported.get("status") != gate["status"]:
            differences.append("status mismatch:{}".format(gate_id))
    return differences


def replay(bundle_path: Path, formal_path: Path):
    root = bundle_path.parent
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    result = aggregate(root, root / "ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml")
    gate_id = bundle["evidence_bundle_metadata"]["gate_id"]
    gate = result["gates"][gate_id]
    if result["status"] != "PASSED" or gate["status"] != "PASSED":
        raise AggregationError("independent raw replay failed")
    if bundle["counts"] != gate["counts"]:
        raise AggregationError("bundle counts differ from independent raw aggregation")
    formal = yaml.safe_load(formal_path.read_text(encoding="utf-8"))
    return {
        "status": "PASSED",
        "gate_section": "CORE0A_gates",
        "gate_id": gate_id,
        "counts": gate["counts"],
        "evidence_bundle_sha256": file_hash(bundle_path),
        "formal_contract_hash": formal["contract_metadata"]["formal_contract_hash"],
        "validator_version": VERSION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-report", type=Path)
    parser.add_argument("--replay-gate-evidence", type=Path)
    parser.add_argument("--formal-contract", type=Path)
    args = parser.parse_args()
    try:
        if args.replay_gate_evidence:
            if not args.formal_contract:
                parser.error("--formal-contract is required for gate replay")
            output = replay(args.replay_gate_evidence, args.formal_contract)
        else:
            if not args.evidence_root or not args.template:
                parser.error("--evidence-root and --template are required")
            output = aggregate(args.evidence_root, args.template)
            if args.compare_report:
                differences = compare_report(output, args.compare_report)
                output["report_differences"] = differences
                if differences:
                    output["status"] = "FAILED"
            if args.output:
                args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0 if output["status"] == "PASSED" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
