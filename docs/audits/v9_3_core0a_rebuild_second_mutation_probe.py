#!/usr/bin/env python3
"""Independently replay required source and positive-E0 mutations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v9_3_core0a_rebuild_second_audit as audit


SOURCE = {
    "service_curve_skip_beta_zero": (
        "    if first[0] != 0:\n", "    if False and first[0] != 0:\n"),
    "service_curve_skip_monotonicity": (
        "        if length and value < previous:\n",
        "        if False and length and value < previous:\n"),
    "remove_processor_truncation": (
        "        min(workload_bound_v9_3(task, w, theta), interference_cap)\n",
        "        workload_bound_v9_3(task, w, theta)\n"),
    "delete_yk_power": (
        "            energy = y_k * target.power\n",
        "            energy = Fraction(0)\n"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_core(path):
    spec = importlib.util.spec_from_file_location("second_audit_mutated_core", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def worker(mutation, module_path):
    core = load_core(module_path)
    if mutation == "service_curve_skip_beta_zero":
        try:
            core.validate_service_curve_v9_3([1, 1], 1)
        except core.V93NumericError:
            return 0
        raise AssertionError("mutated beta(0) validator accepted an illegal curve")
    if mutation == "service_curve_skip_monotonicity":
        try:
            core.validate_service_curve_v9_3([0, 2, 1], 2)
        except core.V93NumericError:
            return 0
        raise AssertionError("mutated monotonicity validator accepted an illegal curve")
    if mutation == "remove_processor_truncation":
        target = core.V93Task("k", 3, 8, 9, 1)
        hp = (core.V93Task("h", 3, 4, 4, 1),)
        assert core.effective_hp_workloads_v9_3(target, hp, 3, {"h": 4}) == (1,), \
            "mutated processor workload lost w-C+1 truncation"
        return 0
    if mutation == "delete_yk_power":
        target = core.V93Task("k", 2, 3, 4, 7)
        assert core.complete_window_envelope_v9_3(target, (), (), 2, 2, 0, 2, {}) == 14, \
            "mutated envelope omitted y_k P_k"
        return 0
    raise ValueError(mutation)


def source_mutations():
    rows = []
    pristine_path = ROOT / "asap_block_rta_v9_3.py"
    pristine = pristine_path.read_text(encoding="utf-8")
    original_hash = digest(pristine_path)
    for mutation, (old, new) in SOURCE.items():
        if pristine.count(old) != 1:
            raise RuntimeError("non-unique source target:" + mutation)
        with tempfile.TemporaryDirectory(prefix="core0a-second-source-mutation-") as name:
            path = Path(name) / pristine_path.name
            path.write_text(pristine.replace(old, new, 1), encoding="utf-8")
            mutated_hash = digest(path)
            process = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker", mutation,
                 "--module", str(path)], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
            path.write_text(pristine, encoding="utf-8")
            restored_hash = digest(path)
            transcript = process.stdout + process.stderr
            rows.append({
                "mutation_id": mutation, "original_hash": original_hash,
                "mutated_hash": mutated_hash, "restored_hash": restored_hash,
                "exit_code": process.returncode,
                "assertion_failure": "AssertionError" in transcript,
                "syntax_or_import_failure": any(x in transcript for x in (
                    "SyntaxError", "ImportError", "ModuleNotFoundError")),
                "detected": process.returncode != 0 and "AssertionError" in transcript
                            and not any(x in transcript for x in (
                                "SyntaxError", "ImportError", "ModuleNotFoundError"))
                            and original_hash == restored_hash
                            and original_hash != mutated_hash,
                "transcript_tail": transcript[-500:],
            })
    return rows


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def refresh_raw_manifest(root, filename, count):
    path = root / filename
    manifest_path = root / "raw_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][filename] = digest(path)
    manifest["row_counts"][filename] = count
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                        sort_keys=True) + "\n", encoding="utf-8")


def finite_result(root):
    tables = {name: audit.read_csv(root, name) for name in audit.RAW_FILES}
    return audit.audit_finite(tables)


def data_mutations(evidence_root):
    outcomes = []
    mutations = (
        "delete_certificate", "release_energy_below_E0",
        "candidate_certification_provisional", "delete_bound_check",
    )
    for mutation in mutations:
        with tempfile.TemporaryDirectory(prefix="core0a-second-data-mutation-") as name:
            root = Path(name) / "evidence"
            shutil.copytree(evidence_root, root)
            if mutation == "delete_bound_check":
                filename = "bound_checks.csv"
            else:
                filename = "release_energy_certificates.csv"
            fields, rows = audit.read_csv(root, filename)
            if mutation == "delete_certificate":
                rows = rows[1:]
            elif mutation == "release_energy_below_E0":
                rows[0]["release_energy"] = str(Fraction(rows[0]["E0"]) - 1)
            elif mutation == "candidate_certification_provisional":
                rows[0]["candidate_jointly_certified"] = "false"
            elif mutation == "delete_bound_check":
                rows = rows[1:]
            write_csv(root / filename, fields, rows)
            refresh_raw_manifest(root, filename, len(rows))
            result = finite_result(root)
            outcomes.append({"mutation_id": mutation,
                             "rejected": bool(result["errors"]),
                             "errors": result["errors"][:5]})
    baseline = finite_result(evidence_root)["N_positive_E0_satisfied_traces"]
    outcomes.append({
        "mutation_id": "reported_positive_E0_count_zero",
        "rejected": baseline != 0,
        "errors": ["reported 0 != independently replayed {}".format(baseline)],
    })
    return outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--worker")
    parser.add_argument("--module", type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.module)
    if args.evidence_root is None:
        parser.error("--evidence-root is required")
    sources = source_mutations()
    data = data_mutations(args.evidence_root)
    ok = all(row["detected"] for row in sources) and all(row["rejected"] for row in data)
    print(json.dumps({"status": "PASSED" if ok else "FAILED",
                      "source_mutations": sources, "positive_E0_mutations": data},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
