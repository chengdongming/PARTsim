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

from core0a_v9_3_evidence_schema import (
    LINEAGE_REQUIRED_CHECK_TYPES, RAW_TABLES, SCHEMA_VERSION, TABLE_SCHEMAS)
from core0a_v9_3_oracles import processor_reference


VERSION = "CORE0A-SECOND-REBUILD-INDEPENDENT-3.0"


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
    expected_superseded = {
        ("dcb55f6a22f4d772a74f94ac7799b79cf5da8541", "d56c2f671b8ea201e6e53a4199cba333f3dcc6eb1e09ff06a1bfa8b76db8dd50", "INVALIDATED"),
        ("01f582b094f376a8e00640e22d0d2f25506d0e35", "a51ceee47c9f0e32a80a23f4c419af1271d35b29522d91b6630812bb362a2995", "INVALIDATED"),
    }
    actual_superseded = {(row.get("commit"), row.get("zip_sha256"), row.get("status")) for row in manifest.get("superseded_core0a_evidence", [])}
    if actual_superseded != expected_superseded or manifest.get("pilot_authorized") is not False:
        raise AggregationError("invalidated CORE-0A evidence list/authorization mismatch")
    if not set(RAW_TABLES) <= set(manifest.get("files", {})):
        raise AggregationError("raw evidence manifest misses schema files")
    for name, digest in manifest.get("files", {}).items():
        if Path(name).name != name or file_hash(root / name) != digest:
            raise AggregationError("raw evidence member hash mismatch: {}".format(name))
    tables = {}
    build = manifest.get("build_identity_hash")
    for name in RAW_TABLES:
        path = root / name
        if file_hash(path) != manifest["files"][name]:
            raise AggregationError("raw evidence member hash mismatch: {}".format(name))
        if name.endswith(".jsonl"):
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            if any(set(row) != set(TABLE_SCHEMAS[name]["fields"]) for row in rows):
                raise AggregationError("raw evidence JSONL fields mismatch: {}".format(name))
            rows = [{key: str(value) for key, value in row.items()} for row in rows]
        else:
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


def audit_search(spec_rows, lookup_rows, trace_rows):
    """Rebuild visits solely from finite ScriptedClosureSpecification rows."""
    specs = {row["specification_id"]: row for row in spec_rows}
    lookup = defaultdict(dict)
    traces = defaultdict(list)
    violations = []
    for row in lookup_rows:
        key = (int(row["w"]), int(row["h"]), int(row["q"]))
        if key in lookup[row["specification_id"]]:
            violations.append([row["specification_id"], "duplicate lookup", key])
        lookup[row["specification_id"]][key] = row
    for row in trace_rows:
        traces[row["specification_id"]].append(row)
    expected_w = expected_h = expected_q = observed_w = observed_h = observed_q = 0
    duplicate_count = 0
    for specification_id, spec in specs.items():
        if spec["case_kind"] not in {"DECLARATIVE_SCRIPTED", "REAL_MATH_SOLVER"}:
            violations.append([specification_id, "opaque callback evidence"])
        w_domain = load_json_text(spec["w_domain_json"], "w domain")
        a_lookup = load_json_text(spec["A_lookup_json"], "A lookup")
        closure = load_json_text(spec["closure_point_json"], "closure point")
        case_lookup = lookup.get(specification_id, {})
        expected_keys = {
            (w, h, q)
            for w in w_domain
            for h in range(0, w - int(a_lookup[str(w)]) + 1)
            for q in range(1, int(a_lookup[str(w)]) + 1)
            if int(a_lookup[str(w)]) <= w
        }
        if set(case_lookup) != expected_keys:
            violations.append([specification_id, "lookup domain", len(expected_keys), len(case_lookup)])
        canonical_lookup = []
        for key in sorted(case_lookup):
            row = case_lookup[key]
            envelope = parse_fraction(row["envelope_value"], "lookup envelope")
            service = parse_fraction(row["service_value"], "lookup service")
            predicate = envelope <= service
            if parse_bool(row["expected_predicate"], "lookup predicate") != predicate:
                violations.append([specification_id, "lookup predicate", key])
            canonical_lookup.append([*key, row["envelope_value"], row["service_value"], "true" if predicate else "false"])
        spec_payload = {
            "specification_id": specification_id,
            "case_kind": spec["case_kind"], "variant": spec["variant"],
            "task": load_json_text(spec["task_json"], "task"),
            "hp": load_json_text(spec["hp_json"], "hp"),
            "lp": load_json_text(spec["lp_json"], "lp"),
            "theta": load_json_text(spec["theta_json"], "theta"),
            "M": int(spec["M"]), "E0": spec["E0"],
            "w_domain": w_domain, "A_lookup": a_lookup,
            "closure_point": closure,
            "expected_result_status": spec["expected_result_status"],
            "lookup": canonical_lookup,
        }
        if semantic_hash("CORE0A:SCRIPTED_CLOSURE_SPECIFICATION:v1", spec_payload) != spec["canonical_specification_hash"]:
            violations.append([specification_id, "canonical specification hash"])
        expected = []
        found = None
        for w in w_domain:
            expected_w += 1
            a_value = int(a_lookup[str(w)])
            if a_value > w:
                expected.append((w, None, None, "W_SKIPPED_A_GT_W", "NOT_APPLICABLE", "NOT_APPLICABLE", "CONTINUE"))
                continue
            for h in range(0, w - a_value + 1):
                expected_h += 1
                valid = True
                for q in range(1, a_value + 1):
                    expected_q += 1
                    row = case_lookup.get((w, h, q))
                    if row is None:
                        valid = False
                        break
                    passed = parse_fraction(row["envelope_value"], "envelope") <= parse_fraction(row["service_value"], "service")
                    q_result = "PASS" if passed else "FAIL"
                    closes = passed and q == a_value
                    expected.append((w, h, q, "Q_CHECK", q_result, "CLOSED" if closes else ("CONTINUE" if passed else "FAIL"), "CANDIDATE" if closes else "CONTINUE"))
                    if not passed:
                        valid = False
                        break
                if valid:
                    found = {"w": w, "h": h, "q": a_value}
                    break
            if found is not None:
                break
        if found != closure:
            violations.append([specification_id, "closure point", closure, found])
        events = sorted(traces.get(specification_id, ()), key=lambda row: int(row["sequence_number"]))
        if [int(row["sequence_number"]) for row in events] != list(range(len(events))):
            violations.append([specification_id, "sequence numbering"])
        actual = [(int(r["w"]), None if r["h"] == "" else int(r["h"]), None if r["q"] == "" else int(r["q"]), r["event_type"], r["q_result"], r["h_result"], r["w_result"]) for r in events]
        if actual != expected:
            violations.append([specification_id, "visit order", expected, actual])
        for row in events:
            if row["q"] == "":
                continue
            key = (int(row["w"]), int(row["h"]), int(row["q"]))
            declared = case_lookup.get(key)
            if declared is None or row["envelope_value"] != declared["envelope_value"] or row["service_value"] != declared["service_value"]:
                violations.append([specification_id, "trace/lookup mismatch", key])
            if int(row["service_index"]) != key[1] + key[2] - 1:
                violations.append([specification_id, "service index", key])
            coverage = key[0] if spec["variant"] == "complete" else key[1] + key[2]
            if int(row["coverage_index"]) != coverage:
                violations.append([specification_id, "coverage index", key])
        coordinates = [(r["w"], r["h"], r["q"]) for r in events]
        duplicate_count += len(coordinates) - len(set(coordinates))
        observed_w += len({r["w"] for r in events})
        observed_h += len({(r["w"], r["h"]) for r in events if r["h"] != ""})
        observed_q += sum(r["q"] != "" for r in events)
    return {
        "N_tasks": len(specs), "N_expected_w": expected_w,
        "N_observed_w": observed_w, "N_expected_h": expected_h,
        "N_observed_h": observed_h, "N_expected_q": expected_q,
        "N_observed_q": observed_q, "N_search_duplicates": duplicate_count,
        "N_search_order_violations": len(violations), "violations": violations[:10],
    }


def validate_fks(tables):
    failures = []

    def check(source, source_fields, target, target_fields):
        target_keys = {tuple(row[field] for field in target_fields) for row in tables[target]}
        for row in tables[source]:
            key = tuple(row[field] for field in source_fields)
            if key not in target_keys:
                failures.append([source, key, target])

    check("search_closure_lookup.csv", ["specification_id"], "search_closure_specifications.csv", ["specification_id"])
    check("search_trace_events.csv", ["specification_id"], "search_closure_specifications.csv", ["specification_id"])
    check("scheduler_event_order_ticks.csv", ["microcase_id"], "scheduler_event_order_cases.csv", ["microcase_id"])
    check("scheduler_event_order_assertions.csv", ["microcase_id", "tick"], "scheduler_event_order_ticks.csv", ["microcase_id", "tick"])
    for source in ("joint_certification_task_inputs.csv", "joint_certification_solver_script.csv",
                   "joint_certification_actual_tasks.csv", "joint_certification_expected_tasks.csv",
                   "joint_certification_assertions.csv", "joint_certification_results.csv"):
        check(source, ["case_id"], "joint_certification_cases.jsonl", ["case_id"])
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


def audit_event_order(cases, ticks, assertions):
    failures = []
    tick_groups = defaultdict(list)
    assertion_map = {(r["microcase_id"], int(r["tick"])): r for r in assertions}
    for row in ticks:
        tick_groups[row["microcase_id"]].append(row)
    for case in cases:
        case_id = case["microcase_id"]
        if case["event_order_specification_id"] != "ASAP_BLOCK_TICK_ORDER_V9_3_FROZEN_1":
            failures.append([case_id, "event order specification"])
        jobs = []
        energy = parse_fraction(case["initial_energy"], "initial energy")
        tasks = load_json_text(case["initial_tasks_json"], "initial tasks")
        service = load_json_text(case["service_curve_json"], "service curve")
        rows = sorted(tick_groups.get(case_id, ()), key=lambda r: int(r["tick"]))
        if [int(r["tick"]) for r in rows] != list(range(len(service))):
            failures.append([case_id, "missing/reordered tick"])
        completed_next = defaultdict(list)
        for row in rows:
            tick = int(row["tick"])
            releases = [dict(item) for item in tasks if int(item["release"]) == tick]
            recorded_releases = load_json_text(row["released_jobs_json"], "released jobs")
            if [{k: v for k, v in item.items() if k != "release"} for item in releases] != recorded_releases:
                failures.append([case_id, tick, "release rows/order"])
            completed = sorted(completed_next.pop(tick, []))
            for item in releases:
                jobs.append({**item, "remaining": int(item["wcet"])})
            remaining_before = {str(job["job_id"]): int(job["remaining"]) for job in jobs if int(job["remaining"]) > 0}
            eligible = []
            for rank in sorted({int(job["priority_rank"]) for job in jobs}):
                pending = [job for job in jobs if int(job["priority_rank"]) == rank and int(job["remaining"]) > 0]
                if pending:
                    eligible.append(min(pending, key=lambda job: (int(job["release"]), str(job["job_id"]))))
            scan, selected = [], []
            available = energy
            stopped = False
            for job in eligible:
                if len(selected) >= int(case["M"]):
                    break
                scan.append(str(job["job_id"]))
                if parse_fraction(str(job["power"]), "power") > available:
                    stopped = True
                    break
                selected.append(job)
                available -= parse_fraction(str(job["power"]), "power")
            for job in selected:
                job["remaining"] -= 1
                if job["remaining"] == 0:
                    completed_next[tick + 1].append(str(job["job_id"]))
            processor_blocked, energy_blocked = [], []
            for job in eligible:
                if job in selected or job["remaining"] == 0:
                    continue
                higher = sum(int(other["priority_rank"]) < int(job["priority_rank"]) for other in selected)
                if higher >= int(case["M"]):
                    processor_blocked.append(str(job["job_id"]))
                elif stopped:
                    energy_blocked.append(str(job["job_id"]))
            harvest = parse_fraction(str(service[tick]), "harvest")
            expected = {
                "energy_before": str(energy),
                "completed_jobs_json": completed,
                "harvested_energy_committed": str(harvest),
                "ready_hol_order_json": [str(job["job_id"]) for job in eligible],
                "eligible_jobs_json": [str(job["job_id"]) for job in eligible],
                "scheduler_scan_order_json": scan,
                "selected_jobs_json": [str(job["job_id"]) for job in selected],
                "consumed_energy": str(energy - available),
                "energy_after": str(available + harvest),
                "job_remaining_before_json": remaining_before,
                "job_remaining_after_json": {str(job["job_id"]): int(job["remaining"]) for job in jobs},
                "processor_blocked_jobs_json": processor_blocked,
                "energy_blocked_jobs_json": energy_blocked,
            }
            for field, value in expected.items():
                actual = load_json_text(row[field], field) if field.endswith("_json") else row[field]
                if actual != value:
                    failures.append([case_id, tick, field, value, actual])
            assertion = assertion_map.get((case_id, tick))
            if assertion is None:
                failures.append([case_id, tick, "missing assertion"])
            else:
                declared = load_json_text(assertion["expected_event"], "expected event")
                observed = load_json_text(assertion["actual_event"], "actual event")
                recomputed_aliases = {
                    "completion_events": completed, "release_events": sorted(str(x["job_id"]) for x in releases),
                    "eligible_hol": expected["eligible_jobs_json"], "scan_order": scan,
                    "execution_set": expected["selected_jobs_json"], "post_tick_energy": expected["energy_after"],
                    "processor_blocked_jobs": processor_blocked, "energy_blocked_jobs": energy_blocked,
                }
                recomputed_subset = {key: recomputed_aliases[key] for key in declared}
                if declared != recomputed_subset or observed != recomputed_subset:
                    failures.append([case_id, tick, "assertion expected/actual mismatch"])
            energy = available + harvest
        expected_assertion_keys = {(case_id, tick) for tick in range(len(service))}
        actual_assertion_keys = {key for key in assertion_map if key[0] == case_id}
        if actual_assertion_keys != expected_assertion_keys:
            failures.append([case_id, "assertion/tick coverage"])
    return len(failures), failures[:10]


def audit_joint(tables):
    cases = {row["case_id"]: row for row in tables["joint_certification_cases.jsonl"]}
    required = {
        "RECURSIVE_FULL_SUCCESS", "PROVISIONAL_PREFIX", "ATOMIC_CERTIFICATION",
        "MIDDLE_NO_CANDIDATE", "TIMEOUT", "NUMERIC_ERROR", "SUFFIX_NOT_EVALUATED",
        "DEPENDENCY_NOT_APPLICABLE", "DIAGNOSTIC_ONLY", "SOURCE_VARIANT_MISMATCH",
        "SOURCE_PROVISIONAL", "FROZEN_VECTOR_MISMATCH", "LOC_CANDIDATE_GT_SOURCE",
        "VALID_DOMAIN_LOC_NO_CANDIDATE", "UNKNOWN_CORE_STATUS",
        "INTERNAL_CONFORMANCE_FAILURE", "FINALIZER_FAILURE_WITHOUT_PARTIAL_CERTIFICATION",
    }
    failures = []
    if {row["case_kind"] for row in cases.values()} != required or len(cases) != 17:
        failures.append(["case coverage"])
    grouped = {}
    for name in ("joint_certification_task_inputs.csv", "joint_certification_solver_script.csv",
                 "joint_certification_actual_tasks.csv", "joint_certification_expected_tasks.csv",
                 "joint_certification_assertions.csv"):
        grouped[name] = defaultdict(list)
        for row in tables[name]:
            grouped[name][row["case_id"]].append(row)
    result_map = {row["case_id"]: row for row in tables["joint_certification_results.csv"]}
    no_call_kinds = {"DEPENDENCY_NOT_APPLICABLE", "SOURCE_VARIANT_MISMATCH", "SOURCE_PROVISIONAL", "FROZEN_VECTOR_MISMATCH"}
    for case_id, case in cases.items():
        inputs = sorted(grouped["joint_certification_task_inputs.csv"].get(case_id, ()), key=lambda r: int(r["priority_rank"]))
        scripts = sorted(grouped["joint_certification_solver_script.csv"].get(case_id, ()), key=lambda r: int(r["call_sequence"]))
        actuals = sorted(grouped["joint_certification_actual_tasks.csv"].get(case_id, ()), key=lambda r: int(r["evaluation_order"]))
        expected_rows = sorted(grouped["joint_certification_expected_tasks.csv"].get(case_id, ()), key=lambda r: int(r["evaluation_order"]))
        result = result_map.get(case_id)
        if len(inputs) != 3 or len(scripts) != 3 or len(actuals) != 3 or len(expected_rows) != 3 or result is None:
            failures.append([case_id, "incomplete replay rows"])
            continue
        kind = case["case_kind"]
        source_kinds = {"FROZEN_VECTOR_MISMATCH", "LOC_CANDIDATE_GT_SOURCE", "VALID_DOMAIN_LOC_NO_CANDIDATE"}
        if kind in source_kinds and any(not row["source_candidate"] or row["source_candidate"] != row["fixed_carry_in"] for row in inputs):
            failures.append([case_id, "source/frozen vector input"])
        expected_tasks = []
        terminal = False
        for script in scripts:
            called = not terminal and kind not in no_call_kinds
            if parse_bool(script["expected_called"], "expected called") != called:
                failures.append([case_id, "solver call sequence", script["task_id"]])
            status = script["solver_outcome"] if called else "NOT_APPLICABLE_DEPENDENCY"
            certification = "PROVISIONAL_NOT_CERTIFIED" if status == "CANDIDATE_FOUND" else ("NOT_APPLICABLE" if not called else "NOT_CERTIFIED")
            if terminal:
                status, certification = "NOT_EVALUATED_AFTER_PREFIX_FAILURE", "NOT_APPLICABLE"
            expected_tasks.append((script["task_id"], status, certification, script["candidate"] if status == "CANDIDATE_FOUND" else ""))
            if called and status != "CANDIDATE_FOUND":
                terminal = True
        if kind == "DIAGNOSTIC_ONLY":
            expected_tasks = [(a, b, "DIAGNOSTIC_ONLY_NOT_CERTIFIED", d) for a, b, _c, d in expected_tasks]
        all_candidates = all(item[1] == "CANDIDATE_FOUND" for item in expected_tasks)
        if all_candidates and kind not in {"DIAGNOSTIC_ONLY", "FINALIZER_FAILURE_WITHOUT_PARTIAL_CERTIFICATION"}:
            expected_tasks = [(a, b, "CERTIFIED", d) for a, b, _c, d in expected_tasks]
        if kind == "FINALIZER_FAILURE_WITHOUT_PARTIAL_CERTIFICATION":
            expected_tasks = [(a, b, "PROVISIONAL_NOT_CERTIFIED", d) for a, b, _c, d in expected_tasks]
        if kind == "LOC_CANDIDATE_GT_SOURCE":
            expected_tasks[0] = (expected_tasks[0][0], "CANDIDATE_FOUND", "PROVISIONAL_NOT_CERTIFIED", expected_tasks[0][3])
            expected_tasks[1] = (expected_tasks[1][0], "CANDIDATE_FOUND", "NOT_CERTIFIED", expected_tasks[1][3])
            expected_tasks[2] = (expected_tasks[2][0], "NOT_EVALUATED_AFTER_PREFIX_FAILURE", "NOT_APPLICABLE", "")
        actual_tuples = [(r["task_id"], r["solver_status"], r["certification_status"], r["candidate"]) for r in actuals]
        expected_table_tuples = [(r["task_id"], r["solver_status"], r["certification_status"], r["candidate"]) for r in expected_rows]
        if actual_tuples != expected_tasks or expected_table_tuples != expected_tasks:
            failures.append([case_id, "task-state derivation", expected_tasks, actual_tuples])
        terminal_status = next((s["solver_outcome"] for s in scripts if parse_bool(s["expected_called"], "called") and s["solver_outcome"] != "CANDIDATE_FOUND"), None)
        expected_solver = "COMPLETED"
        expected_cert = "CERTIFIED_TASKSET"
        expected_proven = "true"
        if kind in no_call_kinds:
            expected_solver, expected_cert, expected_proven = "NOT_APPLICABLE_DEPENDENCY", "NOT_APPLICABLE", "false"
        elif kind == "DIAGNOSTIC_ONLY":
            expected_solver, expected_cert, expected_proven = "COMPLETED", "DIAGNOSTIC_ONLY_NOT_CERTIFIED", "false"
        elif kind == "FINALIZER_FAILURE_WITHOUT_PARTIAL_CERTIFICATION":
            expected_solver, expected_cert, expected_proven = "FINALIZER_ERROR", "NOT_CERTIFIED", "false"
        elif kind in {"LOC_CANDIDATE_GT_SOURCE", "VALID_DOMAIN_LOC_NO_CANDIDATE", "UNKNOWN_CORE_STATUS", "INTERNAL_CONFORMANCE_FAILURE"}:
            expected_solver, expected_cert, expected_proven = "INTERNAL_CONFORMANCE_FAILURE", "NOT_CERTIFIED", "false"
        elif terminal_status:
            expected_solver, expected_cert, expected_proven = terminal_status, "NOT_CERTIFIED", "false"
        if (result["actual_solver_status"], result["actual_certification_status"], result["actual_taskset_proven"]) != (expected_solver, expected_cert, expected_proven):
            failures.append([case_id, "overall state derivation"])
        if result["source_hash_before"] != result["source_hash_after"]:
            failures.append([case_id, "source object mutated"])
        pre = load_json_text(result["pre_finalizer_status"], "pre finalizer")
        post = load_json_text(result["post_finalizer_status"], "post finalizer")
        if expected_proven == "true" and (not pre or not post):
            failures.append([case_id, "missing atomic finalizer states"])
        if expected_proven == "true" and (
            pre != [[row["task_id"], "PROVISIONAL_NOT_CERTIFIED"] for row in actuals]
            or post != [[row["task_id"], "CERTIFIED"] for row in actuals]
        ):
            failures.append([case_id, "finalizer transition mismatch"])
        if kind == "FINALIZER_FAILURE_WITHOUT_PARTIAL_CERTIFICATION" and post:
            failures.append([case_id, "partial finalizer certification"])
        for assertion in grouped["joint_certification_assertions.csv"].get(case_id, ()):
            if assertion["expected"] != assertion["actual"]:
                failures.append([case_id, "assertion mismatch", assertion["assertion_id"]])
    return len(failures), failures[:10]


def audit_lineage(tables):
    rows = tables["lineage_checks.csv"]
    failures = []
    types = {row["check_type"] for row in rows}
    missing = set(LINEAGE_REQUIRED_CHECK_TYPES) - types
    if missing:
        failures.append(["missing check types", sorted(missing)])
    if any(parse_bool(row["violation"], "lineage violation") for row in rows):
        failures.append(["reported lineage violation"])
    if not any(row["check_type"] == "EXECUTION_STATE_VALID" for row in rows):
        failures.append(["state-transition vacuity"])
    finite_ids = {row["taskset_id"] for row in tables["finite_state_tasksets.csv"]}
    request_ids = {row["source_row_id"] for row in rows if row["check_type"] == "REQUEST_ACCOUNTED"}
    state_ids = {row["source_row_id"] for row in rows if row["check_type"] == "EXECUTION_STATE_VALID"}
    if request_ids != finite_ids or state_ids != finite_ids:
        failures.append(["request/state coverage", sorted(finite_ids - request_ids), sorted(finite_ids - state_ids)])
    propagation = [row for row in rows if row["check_type"] == "GENERATION_FAILURE_PROPAGATION"]
    if len(propagation) != 1 or propagation[0]["expected"] != "NOT_RUN_DEPENDENCY" or propagation[0]["actual"] != "NOT_RUN_DEPENDENCY":
        failures.append(["generation failure propagation"])
    graph = defaultdict(list)
    for row in tables["dominance_tasksets.csv"]:
        graph[row["source_analysis_id"]].append(row["local_analysis_id"])
        graph.setdefault(row["local_analysis_id"], [])
    visiting, visited = set(), set()
    def cycle(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        found = any(cycle(child) for child in graph[node])
        visiting.remove(node)
        visited.add(node)
        return found
    has_cycle = any(cycle(node) for node in tuple(graph))
    dag_rows = [row for row in rows if row["check_type"] == "DEPENDENCY_DAG_ACYCLIC"]
    if has_cycle or len(dag_rows) != 1 or dag_rows[0]["actual"] != ("true" if has_cycle else "false"):
        failures.append(["dependency DAG cycle evidence", has_cycle])
    for row in tables["finite_state_tasksets.csv"]:
        if parse_bool(row["taskset_proven"], "taskset proven") != (row["analysis_certification_status"] == "CERTIFIED_TASKSET"):
            failures.append(["taskset_proven conflict", row["taskset_id"]])
    return len(failures), failures[:10]


def audit_mutations(rows, root):
    failures = []
    for row in rows:
        try:
            transcript_ok = all(
                Path(row[field]).name == row[field]
                and (root / row[field]).is_file()
                and file_hash(root / row[field]) == row[digest]
                for field, digest in (("stdout_member_path", "stdout_sha256"), ("stderr_member_path", "stderr_sha256")))
            valid = (
                parse_bool(row["mutation_applied"], "mutation applied")
                and row["original_source_hash"] != row["mutated_source_hash"]
                and int(row["exit_code"]) != 0
                and parse_bool(row["failure_matches_target"], "failure target")
                and not parse_bool(row["syntax_import_failure"], "syntax/import failure")
                and row["observed_failing_assertion_id"] == row["expected_failing_assertion_id"]
                and row["restored_source_hash"] == row["original_source_hash"]
                and parse_bool(row["detected"], "mutation detected")
                and bool(load_json_text(row["argv_json"], "mutation argv"))
                and transcript_ok)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            valid = False
        if not valid:
            failures.append(row.get("mutation_id", "MISSING_ID"))
    required = {
        "delete_release_certificate", "release_energy_below_e0",
        "certified_candidate_provisional", "delete_bound_check",
        "break_certificate_job_fk",
    }
    missing = required - {row.get("mutation_id") for row in rows}
    return len(failures), failures, required, missing


def aggregate(root: Path, template_path: Path):
    manifest, tables = read_tables(root)
    global_failures = []
    workload_mismatch = sum(not parse_bool(row["match"], "workload match") for row in tables["workload_cases.csv"])
    workload_monotonic = sum(not parse_bool(row["passed"], "workload monotonic") for row in tables["workload_monotonicity_checks.csv"])
    processor_mismatch = sum(not parse_bool(row["match"], "processor match") for row in tables["processor_cases.csv"])
    envelope_mismatch = sum(not parse_bool(row["match"], "envelope match") for row in tables["envelope_cases.csv"])
    if any((workload_mismatch, workload_monotonic, processor_mismatch, envelope_mismatch)):
        global_failures.append("independent mathematical oracle mismatch")

    search = audit_search(
        tables["search_closure_specifications.csv"],
        tables["search_closure_lookup.csv"],
        tables["search_trace_events.csv"],
    )
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

    event_failures, event_details = audit_event_order(
        tables["scheduler_event_order_cases.csv"],
        tables["scheduler_event_order_ticks.csv"],
        tables["scheduler_event_order_assertions.csv"],
    )
    state_failures, state_details = audit_joint(tables)
    if event_failures or state_failures:
        global_failures.append("event/state-machine independent replay failure")

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
    positive_e0_invariant_failures = finite_structure_failures
    for row in positive_release_rows:
        expected_satisfied = parse_fraction(row["release_energy"], "release energy") >= parse_fraction(row["E0"], "E0")
        positive_e0_invariant_failures += not parse_bool(row["candidate_jointly_certified"], "jointly certified")
        positive_e0_invariant_failures += parse_bool(row["bound_check_executed"], "bound executed") != expected_satisfied
        positive_e0_invariant_failures += (row["certificate_status"] == "SATISFIED") != expected_satisfied
    if (
        bound_violations
        or inconclusive
        or internal_error
        or finite_structure_failures
        or not positive_certificates
    ):
        global_failures.append("finite-state soundness/non-vacuity failure")
    if positive_e0_invariant_failures:
        global_failures.append("positive-E0 evidence invariant")

    mutation_rows = tables["mutation_runs.csv"]
    mutation_failures, mutation_details, required_positive_mutations, positive_mutation_missing = audit_mutations(mutation_rows, root)
    if len(mutation_rows) != 15 or mutation_failures:
        global_failures.append("real mutation failure")
    if positive_mutation_missing:
        global_failures.append("positive-E0 mutation coverage failure")

    lineage_failures, lineage_details = audit_lineage(tables)
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
            "N_microcases": len(tables["scheduler_event_order_cases.csv"]),
            "N_failures": event_failures,
        },
        "joint_certification_state_machine": {"N_state_cases": len(tables["joint_certification_cases.jsonl"]), "N_failures": state_failures},
        "loc_theta_cw_dominance_invariant": {"N_common_cases": dominance_common, "N_violations": dominance_violations + dominance_duplicates + vector_mismatches},
        "service_curve_contract_checks": {"N_curves": len(tables["service_curve_cases.csv"]), "N_invalid": service_errors},
        "schema_lineage_and_request_state_machine": {
            "N_rows": len(lineage_rows),
            "N_fk_violations": len(fk_failures),
            "N_state_transition_violations": lineage_failures,
            "N_cycle_violations": sum(item and item[0] == "dependency DAG cycle evidence" for item in lineage_details),
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
            "mutations": {"N_runs": len(mutation_rows), "N_failures": mutation_failures, "N_positive_E0_mutations": len(required_positive_mutations - positive_mutation_missing)},
            "lineage": {"N_checks": len(lineage_rows), "N_failures": lineage_failures, "N_fk_failures": len(fk_failures), "details": lineage_details},
            "event_order": {"N_failures": event_failures, "details": event_details},
            "joint_state": {"N_failures": state_failures, "details": state_details},
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
