#!/usr/bin/env python3
"""Second, adversarial CORE-0A replay from raw rows only.

This module deliberately imports no project module.  In particular it does not
import the evidence producer, either bundled oracle/aggregator, the acceptance
report builder, or the mutation harness.  It treats missing replay inputs as an
audit failure instead of trusting producer-authored boolean fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import stat
import zipfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path, PurePosixPath


BUILD = "b420d0244d8409e6e92dd856d35722df39bd2238201fb60cd9479049f3931662"
RAW_FILES = (
    "workload_cases.csv", "workload_monotonicity_checks.csv",
    "processor_cases.csv", "envelope_cases.csv", "search_trace_events.csv",
    "service_curve_cases.csv", "scheduler_event_order_traces.csv",
    "joint_certification_cases.csv", "dominance_tasksets.csv",
    "dominance_task_results.csv", "finite_state_tasksets.csv",
    "finite_state_jobs.csv", "finite_state_ticks.csv",
    "release_energy_certificates.csv", "bound_checks.csv",
    "mutation_runs.csv", "lineage_checks.csv",
)
PK = {
    "workload_cases.csv": ("case_id",),
    "workload_monotonicity_checks.csv": ("check_id",),
    "processor_cases.csv": ("case_id",),
    "envelope_cases.csv": ("case_id", "kind"),
    "search_trace_events.csv": ("task_case_id", "sequence_number"),
    "service_curve_cases.csv": ("curve_case_id",),
    "scheduler_event_order_traces.csv": ("microcase_id", "tick", "assertion_id"),
    "joint_certification_cases.csv": ("state_case_id",),
    "dominance_tasksets.csv": ("taskset_hash",),
    "dominance_task_results.csv": ("taskset_hash", "task_id"),
    "finite_state_tasksets.csv": ("taskset_id",),
    "finite_state_jobs.csv": ("taskset_id", "job_id"),
    "finite_state_ticks.csv": ("taskset_id", "tick"),
    "release_energy_certificates.csv": ("taskset_id", "job_id"),
    "bound_checks.csv": ("taskset_id", "job_id"),
    "mutation_runs.csv": ("mutation_id",),
    "lineage_checks.csv": ("check_id",),
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def semantic_hash(domain: str, value) -> str:
    return hashlib.sha256(domain.encode() + b"\0" + canonical(value)).hexdigest()


def truth(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError("non-canonical boolean: {!r}".format(value))
    return value == "true"


def frac(value) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("non-exact value")
    return Fraction(value)


def task(value):
    return {
        "name": value["task_id"], "C": int(value["C"]),
        "D": int(value["D"]), "T": int(value["T"]),
        "P": frac(value["P"]),
    }


def workload(t, length: int, theta: int) -> int:
    shifted = length + theta - t["C"]
    jobs = shifted // t["T"]
    residual = shifted - jobs * t["T"]
    return jobs * t["C"] + min(t["C"], residual)


def processor(target, hp, w: int, processors: int, theta) -> int:
    cap = max(0, w - target["C"] + 1)
    bars = [min(workload(t, w, int(theta[t["name"]])), cap) for t in hp]
    answer = 0
    for delay in range(sum(bars) // processors + 1):
        if sum(min(value, delay) for value in bars) >= processors * delay:
            answer = delay
    return answer


def envelope(kind, target, hp, lp, w, q, h, processors, theta):
    coverage = w if kind == "complete" else q + h
    hp_caps = [min(workload(t, coverage, int(theta[t["name"]])), q + h)
               for t in hp]
    best = Fraction(0)
    for yk in range(min(target["C"], q) + 1):
        lp_caps = [min(workload(t, coverage, t["D"]), yk) for t in lp]
        hp_ranges = [range(value + 1) for value in hp_caps]
        lp_ranges = [range(value + 1) for value in lp_caps]
        for hv in itertools.product(*hp_ranges) if hp_ranges else [()]:
            for lv in itertools.product(*lp_ranges) if lp_ranges else [()]:
                if sum(lv) > (processors - 1) * yk:
                    continue
                if yk + sum(hv) + sum(lv) > processors * (q + h):
                    continue
                energy = yk * target["P"]
                energy += sum(units * item["P"] for units, item in zip(hv, hp))
                energy += sum(units * item["P"] for units, item in zip(lv, lp))
                best = max(best, energy)
    return best


def read_csv(root: Path, name: str):
    with (root / name).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def zip_audit(path: Path):
    errors = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            errors.append("duplicate ZIP member")
        for item in infos:
            pure = PurePosixPath(item.filename)
            if pure.is_absolute() or ".." in pure.parts or "\\" in item.filename:
                errors.append("unsafe ZIP path:" + item.filename)
            mode = item.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG}:
                errors.append("non-regular ZIP member:" + item.filename)
            try:
                data = archive.read(item)
                data.decode("utf-8")
            except UnicodeDecodeError:
                errors.append("non-UTF8 member:" + item.filename)
            if data and not data.endswith(b"\n"):
                errors.append("missing final newline:" + item.filename)
            if b"\r\n" in data:
                errors.append("CRLF member:" + item.filename)
        bad_crc = archive.testzip()
        if bad_crc:
            errors.append("bad CRC:" + bad_crc)
    return {"members": len(names), "errors": errors}


def raw_integrity(root: Path, tables):
    manifest = json.loads((root / "raw_evidence_manifest.json").read_text(encoding="utf-8"))
    errors = []
    details = {}
    for name in RAW_FILES:
        path = root / name
        fields, rows = tables[name]
        keys = [tuple(row[field] for field in PK[name]) for row in rows]
        item_errors = []
        if not path.is_file():
            item_errors.append("missing")
        if manifest["files"].get(name) != sha256_bytes(path.read_bytes()):
            item_errors.append("manifest hash")
        if manifest["row_counts"].get(name) != len(rows):
            item_errors.append("manifest row count")
        if len(keys) != len(set(keys)):
            item_errors.append("duplicate PK")
        if keys != sorted(keys):
            item_errors.append("non-canonical PK order")
        if any(row.get("build_identity_hash") != BUILD for row in rows):
            item_errors.append("build identity")
        if not fields or not rows:
            item_errors.append("empty header/data")
        details[name] = {"rows": len(rows), "errors": item_errors}
        errors.extend(name + ":" + value for value in item_errors)
    # Raw-table foreign keys, recomputed rather than trusting lineage_checks.csv.
    fk_specs = (
        ("dominance_task_results.csv", ("taskset_hash",), "dominance_tasksets.csv", ("taskset_hash",)),
        ("finite_state_jobs.csv", ("taskset_id",), "finite_state_tasksets.csv", ("taskset_id",)),
        ("finite_state_ticks.csv", ("taskset_id",), "finite_state_tasksets.csv", ("taskset_id",)),
        ("release_energy_certificates.csv", ("taskset_id", "job_id"), "finite_state_jobs.csv", ("taskset_id", "job_id")),
        ("bound_checks.csv", ("taskset_id", "job_id"), "finite_state_jobs.csv", ("taskset_id", "job_id")),
    )
    for source, sf, target, tf in fk_specs:
        targets = {tuple(row[x] for x in tf) for row in tables[target][1]}
        missing = [tuple(row[x] for x in sf) for row in tables[source][1]
                   if tuple(row[x] for x in sf) not in targets]
        if missing:
            errors.append("FK {}->{}:{}".format(source, target, len(missing)))
    return {"errors": errors, "files": details}


def audit_workload(tables):
    rows = tables["workload_cases.csv"][1]
    by_id = {row["case_id"]: row for row in rows}
    mismatch = 0
    for row in rows:
        t = {"C": int(row["C"]), "D": int(row["D"]), "T": int(row["T"])}
        expected = workload(t, int(row["L"]), int(row["theta"]))
        mismatch += expected != int(row["production_value"])
        mismatch += expected != int(row["oracle_value"])
    monotonic = tables["workload_monotonicity_checks.csv"][1]
    monotonic_errors = 0
    for row in monotonic:
        left, right = by_id[row["left_case_id"]], by_id[row["right_case_id"]]
        ok = int(left["production_value"]) <= int(right["production_value"])
        monotonic_errors += not ok or not truth(row["passed"])
    return {"N_cases": len(rows), "N_mismatches": mismatch,
            "N_monotonicity_checks": len(monotonic),
            "N_monotonicity_violations": monotonic_errors}


def audit_processor(tables):
    rows = tables["processor_cases.csv"][1]
    mismatch = 0
    domains = Counter()
    for row in rows:
        target = task(json.loads(row["target_json"]))
        hp = [task(value) for value in json.loads(row["hp_json"])]
        theta = json.loads(row["theta_json"])
        expected = processor(target, hp, int(row["w"]), int(row["M"]), theta)
        mismatch += expected != int(row["production_value"])
        mismatch += expected != int(row["oracle_value"])
        domains[row["domain"]] += 1
    return {"N_instances": len(rows), "N_mismatches": mismatch,
            "domains": dict(domains)}


def audit_envelope(tables):
    rows = tables["envelope_cases.csv"][1]
    mismatch = 0
    domains = defaultdict(set)
    coverage = Counter()
    for row in rows:
        target = task(json.loads(row["target_json"]))
        hp = [task(value) for value in json.loads(row["hp_json"])]
        lp = [task(value) for value in json.loads(row["lp_json"])]
        theta = json.loads(row["theta_json"])
        expected = envelope(row["kind"], target, hp, lp, int(row["w"]),
                            int(row["q"]), int(row["h"]), int(row["M"]), theta)
        mismatch += expected != frac(row["production_value"])
        mismatch += expected != frac(row["oracle_value"])
        domains[row["domain"]].add(row["case_id"])
        coverage["yk_zero_possible"] += int(row["q"]) >= 1
        coverage["hp_nonempty"] += bool(hp)
        coverage["lp_nonempty"] += bool(lp)
        coverage["M1"] += int(row["M"]) == 1
        coverage["Mgt1"] += int(row["M"]) > 1
    return {"N_rows": len(rows), "N_mismatches": mismatch,
            "N_exhaustive_instances": len(domains["exhaustive"]),
            "N_random_boundary_instances": len(domains["random"]),
            "coverage": dict(coverage)}


def audit_dominance(tables):
    tasksets = tables["dominance_tasksets.csv"][1]
    results = tables["dominance_task_results.csv"][1]
    errors = []
    semantic_hashes = []
    for row in tasksets:
        semantic = {
            "tasks": json.loads(row["tasks_json"]),
            "priority_order": json.loads(row["priority_order_json"]),
            "M": int(row["processors"]), "E0": row["E0"],
            "service_curve": json.loads(row["service_curve_json"]),
        }
        actual = semantic_hash("CORE0A:DOMINANCE:TASKSET", semantic)
        semantic_hashes.append(actual)
        if actual != row["taskset_hash"]:
            errors.append("taskset hash:" + row["taskset_hash"])
        source = json.loads(row["source_vector_json"])
        local = json.loads(row["local_frozen_vector_json"])
        if semantic_hash("CORE0A:DOMINANCE:VECTOR", source) != row["source_vector_hash"]:
            errors.append("source vector hash:" + row["taskset_hash"])
        if semantic_hash("CORE0A:DOMINANCE:VECTOR", local) != row["local_vector_hash"]:
            errors.append("local vector hash:" + row["taskset_hash"])
        if source != local or row["source_certification_status"] != "CERTIFIED_TASKSET" or not truth(row["joint_certified"]):
            errors.append("source/local certification:" + row["taskset_hash"])
    violations = sum(int(row["local_candidate"]) > int(row["source_candidate"])
                     for row in results if truth(row["candidate_compared"]))
    return {"N_requested_cases": len(tasksets),
            "N_unique_semantic_hashes": len(set(semantic_hashes)),
            "N_duplicate_tasksets": len(tasksets) - len(set(semantic_hashes)),
            "N_candidate_comparisons": sum(truth(row["candidate_compared"]) for row in results),
            "N_violations": violations, "errors": errors,
            "task_count_distribution": dict(Counter(len(json.loads(row["tasks_json"])) for row in tasksets))}


def audit_finite(tables):
    tasksets = {row["taskset_id"]: row for row in tables["finite_state_tasksets.csv"][1]}
    job_rows = {(row["taskset_id"], row["job_id"]): row for row in tables["finite_state_jobs.csv"][1]}
    ticks_by = defaultdict(list)
    for row in tables["finite_state_ticks.csv"][1]:
        ticks_by[row["taskset_id"]].append(row)
    errors = []
    replay_jobs = {}
    total_processor = total_energy = 0
    for taskset_id, spec in tasksets.items():
        tasks = {value["task_id"]: task(value) for value in json.loads(spec["tasks_json"])}
        processors = int(spec["processors"])
        rows = sorted(ticks_by[taskset_id], key=lambda row: int(row["tick"]))
        if [int(row["tick"]) for row in rows] != list(range(int(spec["observation_horizon"]))):
            errors.append("tick sequence:" + taskset_id)
        state = {}
        energy = frac(spec["E0"])
        pending_completion = defaultdict(list)
        for row in rows:
            tick = int(row["tick"])
            if frac(row["start_energy"]) != energy:
                errors.append("start energy:{}:{}".format(taskset_id, tick))
            completions = sorted(pending_completion.pop(tick, []))
            if json.loads(row["completion_events_json"]) != completions:
                errors.append("completion event:{}:{}".format(taskset_id, tick))
            releases = json.loads(row["release_events_json"])
            for job_id in releases:
                task_id, release_text = job_id.rsplit("@", 1)
                item = tasks[task_id]
                state[job_id] = {"task": item, "release": int(release_text),
                                 "remaining": item["C"], "completion": None,
                                 "release_energy": energy, "pb": 0, "eb": 0}
            eligible = []
            for name in sorted(tasks, key=lambda value: list(tasks).index(value)):
                pending = [(jid, value) for jid, value in state.items()
                           if value["task"]["name"] == name and value["remaining"] > 0]
                if pending:
                    eligible.append(min(pending, key=lambda pair: (pair[1]["release"], pair[0])))
            eligible_ids = [pair[0] for pair in eligible]
            if json.loads(row["eligible_hol_json"]) != eligible_ids:
                errors.append("eligible HOL:{}:{}".format(taskset_id, tick))
            selected = []
            scan = []
            available = energy
            stopped = False
            for job_id, value in eligible:
                if len(selected) >= processors:
                    break
                scan.append(job_id)
                if value["task"]["P"] > available:
                    stopped = True
                    break
                selected.append(job_id)
                available -= value["task"]["P"]
            if json.loads(row["scan_order_json"]) != scan or json.loads(row["execution_set_json"]) != selected:
                errors.append("scan/execution:{}:{}".format(taskset_id, tick))
            for job_id in selected:
                value = state[job_id]
                value["remaining"] -= 1
                if value["remaining"] == 0:
                    value["completion"] = tick + 1
                    pending_completion[tick + 1].append(job_id)
            pb, eb = [], []
            for job_id, value in eligible:
                if job_id in selected or value["remaining"] == 0:
                    continue
                rank = list(tasks).index(value["task"]["name"])
                higher = sum(list(tasks).index(state[x]["task"]["name"]) < rank for x in selected)
                if higher >= processors:
                    value["pb"] += 1; pb.append(job_id); total_processor += 1
                elif stopped:
                    value["eb"] += 1; eb.append(job_id); total_energy += 1
            if json.loads(row["processor_blocked_jobs_json"]) != pb or json.loads(row["energy_blocked_jobs_json"]) != eb:
                errors.append("blocking:{}:{}".format(taskset_id, tick))
            consumed = energy - available
            service = json.loads(spec["service_curve_json"])
            harvest = frac(service[1]) if len(service) > 1 else Fraction(0)
            energy = available + harvest
            if frac(row["energy_consumed"]) != consumed or frac(row["post_tick_energy"]) != energy:
                errors.append("energy transition:{}:{}".format(taskset_id, tick))
        for job_id, value in state.items():
            replay_jobs[(taskset_id, job_id)] = value
    for key, row in job_rows.items():
        value = replay_jobs.get(key)
        if value is None:
            errors.append("missing replay job:" + repr(key)); continue
        completion = "" if value["completion"] is None else str(value["completion"])
        response = "" if value["completion"] is None else str(value["completion"] - value["release"])
        if completion != row["completion"] or response != row["response_time"]:
            errors.append("job completion:" + repr(key))
        if str(value["pb"]) != row["processor_blocking_ticks"] or str(value["eb"]) != row["energy_blocking_ticks"]:
            errors.append("job blocking:" + repr(key))
    certificates = {(row["taskset_id"], row["job_id"]): row for row in tables["release_energy_certificates.csv"][1]}
    bounds = {(row["taskset_id"], row["job_id"]): row for row in tables["bound_checks.csv"][1]}
    positive = []
    violations = 0
    if set(certificates) != set(job_rows):
        errors.append("certificate/job key set mismatch")
    for key, row in certificates.items():
        job = job_rows.get(key)
        if job is None:
            continue
        if (row["release"] != job["release"] or row["release_energy"] != job["release_energy"]
                or row["E0"] != job["E0"]):
            errors.append("certificate/job value mismatch:" + repr(key))
        satisfied = frac(row["release_energy"]) >= frac(row["E0"])
        if (row["certificate_status"] == "SATISFIED") != satisfied:
            errors.append("certificate predicate mismatch:" + repr(key))
        if truth(row["positive_E0"]) != (frac(row["E0"]) > 0):
            errors.append("positive E0 flag mismatch:" + repr(key))
        if truth(row["candidate_jointly_certified"]) != truth(tasksets[key[0]]["taskset_proven"]):
            errors.append("candidate certification mismatch:" + repr(key))
        executed = truth(row["bound_check_executed"])
        if executed != (key in bounds):
            errors.append("certificate/bound cardinality:" + repr(key))
        if (frac(row["E0"]) > 0 and frac(row["release_energy"]) >= frac(row["E0"])
                and truth(row["candidate_jointly_certified"]) and executed
                and row["certificate_status"] == "SATISFIED"):
            positive.append(key)
    for key, row in bounds.items():
        actual_violation = (not row["response_time"] or
                            int(row["response_time"]) > int(row["candidate"]))
        violations += actual_violation
        if truth(row["violation"]) != actual_violation:
            errors.append("bound predicate:" + repr(key))
    return {"N_instances": len(tasksets),
            "N_complete": sum(truth(row["enumeration_complete"]) for row in tasksets.values()),
            "N_inconclusive": sum(bool(row["inconclusive_reason"]) for row in tasksets.values()),
            "N_internal_error": sum(truth(row["internal_error"]) for row in tasksets.values()),
            "N_jobs": len(job_rows), "N_ticks": sum(len(value) for value in ticks_by.values()),
            "N_certificates": len(certificates), "N_bound_checks": len(bounds),
            "N_certified_bound_violations": violations,
            "N_positive_E0_satisfied_traces": len(set(positive)),
            "N_processor_interference_cases": total_processor,
            "N_energy_blocking_cases": total_energy, "errors": errors}


def unconfirmable(tables):
    joint_fields = set(tables["joint_certification_cases.csv"][0])
    joint_required = {"tasks_json", "source_input_json", "solver_script_json",
                      "actual_result_json", "assertions_json"}
    search_fields = set(tables["search_trace_events.csv"][0])
    event_fields = set(tables["scheduler_event_order_traces.csv"][0])
    lineage_types = Counter(row["check_type"] for row in tables["lineage_checks.csv"][1])
    required_lineage = {
        "REQUEST_ACCOUNTING", "EXECUTION_STATE", "GENERATION_FAILURE_DOWNSTREAM",
        "DEPENDENCY_DAG", "DAG_CYCLE", "ANALYSIS_SOURCE_IDENTITY",
        "TASK_RESULT_HASH", "FAILURE_PROVENANCE", "THEORY_CONTRACT_BUILD_HASH",
        "CANONICAL_COLUMN_ORDER",
    }
    return {
        "joint_certification_state_machine": {
            "missing_fields": sorted(joint_required - joint_fields),
            "reason": "raw rows contain producer assertions, not replayable API inputs/outputs",
        },
        "full_w_q_h_scan": {
            "missing_fields": sorted({"envelope_function_identity", "case_label"} - search_fields),
            "reason": "two cases use injected envelope callbacks whose definitions are absent from raw rows",
        },
        "event_order_and_scheduler_semantics_microcases": {
            "missing_fields": sorted({"processors"} - event_fields),
            "reason": "M is required to replay scan/processor-blocking semantics",
        },
        "schema_lineage_and_request_state_machine": {
            "present_types": dict(lineage_types),
            "missing_types": sorted(required_lineage - set(lineage_types)),
            "reason": "zero state/cycle violations is vacuous because no such check rows exist",
        },
        "mutation_runs": {
            "missing_fields": ["test_command_argv", "failure_assertion", "stdout", "stderr"],
            "reason": "raw rows cannot establish that exit 1 was caused by the target mutation",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tables = {name: read_csv(args.root, name) for name in RAW_FILES}
    workload_result = audit_workload(tables)
    processor_result = audit_processor(tables)
    envelope_result = audit_envelope(tables)
    dominance_result = audit_dominance(tables)
    finite_result = audit_finite(tables)
    missing = unconfirmable(tables)
    result = {
        "audit_version": "CORE0A-SECOND-ADVERSARIAL-1.0",
        "independence": {"project_imports": [], "producer_summary_used_as_input": False},
        "zip_integrity": zip_audit(args.zip_path),
        "raw_integrity": raw_integrity(args.root, tables),
        "workload": workload_result, "processor": processor_result,
        "envelope": envelope_result, "dominance": dominance_result,
        "finite_state": finite_result, "unconfirmable": missing,
        "lineage_53_distribution": dict(Counter(row["check_type"] for row in tables["lineage_checks.csv"][1])),
        "service_curve_rows": {
            "N_curves": len(tables["service_curve_cases.csv"][1]),
            "N_expected_valid": sum(truth(row["expected_valid"]) for row in tables["service_curve_cases.csv"][1]),
            "N_expected_invalid": sum(not truth(row["expected_valid"]) for row in tables["service_curve_cases.csv"][1]),
            "N_illegal_candidates": sum(not truth(row["expected_valid"]) and truth(row["candidate_returned"]) for row in tables["service_curve_cases.csv"][1]),
            "N_illegal_certifications": sum(not truth(row["expected_valid"]) and truth(row["certification_returned"]) for row in tables["service_curve_cases.csv"][1]),
        },
        "mutation_rows": {
            "N_runs": len(tables["mutation_runs.csv"][1]),
            "N_structural_failures": sum(not (truth(row["mutation_applied"]) and row["original_file_hash"] != row["mutated_file_hash"] and int(row["test_exit_code"]) != 0 and row["restored_file_hash"] == row["original_file_hash"]) for row in tables["mutation_runs.csv"][1]),
        },
    }
    fatal = []
    if result["zip_integrity"]["errors"]: fatal.append("zip_integrity")
    if result["raw_integrity"]["errors"]: fatal.append("raw_integrity")
    if workload_result["N_mismatches"] or workload_result["N_monotonicity_violations"]: fatal.append("workload")
    if processor_result["N_mismatches"]: fatal.append("processor")
    if envelope_result["N_mismatches"]: fatal.append("envelope")
    if dominance_result["errors"] or dominance_result["N_violations"]: fatal.append("dominance")
    if finite_result["errors"] or finite_result["N_certified_bound_violations"]: fatal.append("finite_state")
    fatal.extend(sorted(missing))
    result["fatal_findings"] = fatal
    result["status"] = "FAILED" if fatal else "PASSED"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "fatal_findings": fatal}, sort_keys=True))
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
