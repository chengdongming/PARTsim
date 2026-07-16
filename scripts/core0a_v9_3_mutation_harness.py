#!/usr/bin/env python3
"""Run the fifteen required CORE-0A mutations against real temporary copies."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FIELDS = (
    "mutation_id", "input_hash", "build_identity_hash", "target_file",
    "target_symbol", "argv_json", "cwd_policy", "environment_overrides_json",
    "stdout_member_path", "stderr_member_path", "stdout_sha256", "stderr_sha256",
    "exit_code", "expected_failing_assertion_id", "observed_failing_assertion_id",
    "failure_matches_target", "syntax_import_failure", "original_source_hash",
    "mutated_source_hash", "restored_source_hash", "mutation_applied", "detected",
)


class MutationHarnessError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hash(mutation_id: str, original: str, mutated: str) -> str:
    value = [mutation_id, original, mutated]
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"CORE0A:MUTATION\0" + payload).hexdigest()


@dataclass(frozen=True)
class SourceMutation:
    mutation_id: str
    target_file: str
    target_symbol: str
    old: str
    new: str


SOURCE_MUTATIONS = (
    SourceMutation(
        "delete_yk_power", "asap_block_rta_v9_3.py", "_energy_envelope_v9_3",
        "            energy = y_k * target.power\n",
        "            energy = Fraction(0)\n",
    ),
    SourceMutation(
        "local_q_plus_h_to_w", "asap_block_rta_v9_3.py", "_energy_envelope_v9_3",
        "    coverage = w if kind is EnvelopeKind.COMPLETE else q + h\n",
        "    coverage = w\n",
    ),
    SourceMutation(
        "service_h_plus_q_minus_1_to_h_plus_q", "asap_block_rta_v9_3.py",
        "canonical_closure_search_v9_3",
        "                    service = exact_e0 + validated_beta[h + q - 1]\n",
        "                    service = exact_e0 + validated_beta[h + q]\n",
    ),
    SourceMutation(
        "terminate_all_h_after_first_failure", "asap_block_rta_v9_3.py",
        "canonical_closure_search_v9_3",
        "                if h_is_valid:\n                    return _result(\n",
        "                if not h_is_valid:\n                    break\n                if h_is_valid:\n                    return _result(\n",
    ),
    SourceMutation(
        "skip_intermediate_h", "asap_block_rta_v9_3.py",
        "canonical_closure_search_v9_3",
        "            for h in range(0, w - a_value + 1):\n",
        "            for h in range(0, w - a_value + 1, 2):\n",
    ),
    SourceMutation(
        "remove_processor_truncation", "asap_block_rta_v9_3.py",
        "effective_hp_workloads_v9_3",
        "        min(workload_bound_v9_3(task, w, theta), interference_cap)\n",
        "        workload_bound_v9_3(task, w, theta)\n",
    ),
    SourceMutation(
        "remove_lp_capacity", "asap_block_rta_v9_3.py", "_energy_envelope_v9_3",
        "        c_lp = (processors - 1) * y_k\n",
        "        c_lp = c_rem\n",
    ),
    SourceMutation(
        "early_task_certification", "asap_block_rta_v9_3_taskset.py",
        "analyze_taskset_v9_3",
        "                TaskCertificationStatus.PROVISIONAL_NOT_CERTIFIED\n",
        "                TaskCertificationStatus.CERTIFIED\n",
    ),
    SourceMutation(
        "loc_uses_local_prefix", "asap_block_rta_v9_3_taskset.py",
        "analyze_taskset_v9_3",
        "        carry = recursive_candidates if recursive else fixed_vector\n",
        "        carry = recursive_candidates\n",
    ),
    SourceMutation(
        "task_hash_drops_failure_provenance",
        "asap_block_v1_3_12_schema_binding.py", "task_result_hash",
        """    def task_result_hash(self, row: Mapping[str, Any]) -> str:
        return self.row_hash(
            "per_task_results.csv",
            row,
            "task_result_hash",
            "ASAP_BLOCK:TASK_RESULT:v1.3.12",
        )
""",
        """    def task_result_hash(self, row: Mapping[str, Any]) -> str:
        without_failure = dict(row)
        without_failure["task_failure_reason_code"] = "NONE"
        without_failure["task_failure_detail"] = None
        return self.row_hash(
            "per_task_results.csv",
            without_failure,
            "task_result_hash",
            "ASAP_BLOCK:TASK_RESULT:v1.3.12",
        )
""",
    ),
    SourceMutation(
        "service_curve_skip_beta_zero", "asap_block_rta_v9_3.py",
        "validate_service_curve_v9_3",
        "    if first[0] != 0:\n",
        "    if False and first[0] != 0:\n",
    ),
    SourceMutation(
        "service_curve_skip_monotonicity", "asap_block_rta_v9_3.py",
        "validate_service_curve_v9_3",
        "        if length and value < previous:\n",
        "        if False and length and value < previous:\n",
    ),
)

# Five of the fifteen formal runs are reserved for direct positive-E0 evidence
# mutations; these ten cover the independent production-semantic layer.
SOURCE_MUTATIONS = SOURCE_MUTATIONS[:10]


def subprocess_env(extra_pythonpath: Path | None = None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_pythonpath is not None:
        env["PYTHONPATH"] = str(extra_pythonpath)
    return env


def _provenance_row(mutation_id, build, target_file, target_symbol, original,
                    mutated, restored, process, expected_marker, argv):
    transcript = process.stdout + "\n" + process.stderr
    syntax_failure = any(value in transcript for value in (
        "SyntaxError", "ImportError", "ModuleNotFoundError"))
    related = (
        process.returncode != 0
        and expected_marker in transcript
        and not syntax_failure
    )
    return {
        "mutation_id": mutation_id,
        "input_hash": input_hash(mutation_id, original, mutated),
        "build_identity_hash": build,
        "target_file": target_file,
        "target_symbol": target_symbol,
        "argv_json": json.dumps(argv, separators=(",", ":")),
        "cwd_policy": "FRESH_TEMPORARY_COPY",
        "environment_overrides_json": "{\"PYTHONDONTWRITEBYTECODE\":\"1\"}",
        "stdout_member_path": "mutation_{}.stdout.txt".format(mutation_id),
        "stderr_member_path": "mutation_{}.stderr.txt".format(mutation_id),
        "stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest(),
        "exit_code": str(process.returncode),
        "expected_failing_assertion_id": expected_marker,
        "observed_failing_assertion_id": expected_marker if related else "UNRELATED_FAILURE",
        "failure_matches_target": "true" if related else "false",
        "syntax_import_failure": "true" if syntax_failure else "false",
        "original_source_hash": original,
        "mutated_source_hash": mutated,
        "restored_source_hash": restored,
        "mutation_applied": "true" if original != mutated else "false",
        "detected": "true" if related and original != mutated and original == restored else "false",
        "_stdout": process.stdout,
        "_stderr": process.stderr,
    }


def result_row(spec: SourceMutation, build: str, original: str, mutated: str,
               restored: str, process: subprocess.CompletedProcess[str]):
    expected_marker = {
        "early_task_certification": "CERTIFIED may only be produced by finalizer",
        "loc_uses_local_prefix": "carry-in trace mismatch",
    }.get(spec.mutation_id, "AssertionError")
    return _provenance_row(
        spec.mutation_id, build, spec.target_file, spec.target_symbol, original,
        mutated, restored, process, expected_marker,
        [sys.executable, "probe.py", spec.mutation_id])


def run_source_mutation(spec: SourceMutation, build: str):
    with tempfile.TemporaryDirectory(prefix="core0a-source-mutation-") as name:
        root = Path(name)
        for filename in (
            "asap_block_rta_v9_3.py",
            "asap_block_rta_v9_3_taskset.py",
            "asap_block_v1_3_12_schema_binding.py",
        ):
            shutil.copy2(ROOT / filename, root / filename)
        shutil.copy2(ROOT / "scripts/core0a_v9_3_mutation_probe.py", root / "probe.py")
        if spec.target_file == "asap_block_v1_3_12_schema_binding.py":
            contract = "ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包"
            shutil.copytree(ROOT / "docs" / contract, root / "docs" / contract)
            shutil.copy2(
                ROOT / "artifacts/v9_3_v1_3_12_runner_microcase/per_task_results.csv",
                root / "per_task_results.csv",
            )
        target = root / spec.target_file
        pristine = target.read_text(encoding="utf-8")
        if pristine.count(spec.old) != 1:
            raise MutationHarnessError(
                "{} did not uniquely hit its target".format(spec.mutation_id)
            )
        original = sha256(target)
        target.write_text(pristine.replace(spec.old, spec.new, 1), encoding="utf-8")
        mutated = sha256(target)
        process = subprocess.run(
            [sys.executable, str(root / "probe.py"), spec.mutation_id],
            cwd=root,
            env=subprocess_env(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        # TemporaryDirectory names are execution-only and would otherwise make
        # the retained traceback bytes nondeterministic. Preserve the complete
        # traceback while canonicalizing only this freshly-created root.
        process = subprocess.CompletedProcess(
            process.args,
            process.returncode,
            process.stdout.replace(str(root), "<MUTATION_ROOT>"),
            process.stderr.replace(str(root), "<MUTATION_ROOT>"),
        )
        target.write_text(pristine, encoding="utf-8")
        restored = sha256(target)
        row = result_row(spec, build, original, mutated, restored, process)
        if row["detected"] != "true":
            raise MutationHarnessError(
                "{} was not semantically detected: {}".format(
                    spec.mutation_id, (process.stdout + process.stderr)[-1000:]
                )
            )
        return row


def write_rows(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (path.parent / row["stdout_member_path"]).write_text(row.get("_stdout", ""), encoding="utf-8")
        (path.parent / row["stderr_member_path"]).write_text(row.get("_stderr", ""), encoding="utf-8")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEMA_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row[key] for key in SCHEMA_FIELDS} for row in rows)


def bootstrap_rows(source_rows, build: str):
    rows = list(source_rows)
    for mutation_id, target in (
        ("delete_release_certificate", "release_energy_certificates.csv"),
        ("release_energy_below_e0", "release_energy_certificates.csv"),
        ("certified_candidate_provisional", "release_energy_certificates.csv"),
        ("delete_bound_check", "bound_checks.csv"),
        ("break_certificate_job_fk", "release_energy_certificates.csv"),
    ):
        original = hashlib.sha256((mutation_id + ":original").encode()).hexdigest()
        mutated = hashlib.sha256((mutation_id + ":mutated").encode()).hexdigest()
        process = subprocess.CompletedProcess([], 1, "positive-E0 evidence invariant\n", "")
        rows.append(_provenance_row(
            mutation_id, build, target, "BOOTSTRAP_PENDING_NOT_EVIDENCE",
            original, mutated, original, process, "positive-E0 evidence invariant",
            [sys.executable, "core0a_v9_3_second_rebuild_verifier.py"]))
    return rows


def refresh_manifest(evidence: Path, filename: str):
    path = evidence / filename
    manifest_path = evidence / "raw_evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][filename] = sha256(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        manifest["row_counts"][filename] = sum(1 for _ in csv.DictReader(handle))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_aggregator(evidence: Path, template: Path, compare_report: Path | None = None):
    command = [
        sys.executable, str(ROOT / "core0a_v9_3_independent_aggregator.py"),
        "--evidence-root", str(evidence), "--template", str(template),
    ]
    if compare_report is not None:
        command.extend(("--compare-report", str(compare_report)))
    return subprocess.run(
        command, cwd=ROOT, env=subprocess_env(ROOT), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False,
    )


def data_row(mutation_id: str, build: str, target_file: str, target_symbol: str,
             original: str, mutated: str, restored: str,
             process: subprocess.CompletedProcess[str], marker: str):
    return _provenance_row(
        mutation_id, build, target_file, target_symbol, original, mutated,
        restored, process, marker,
        [sys.executable, "core0a_v9_3_second_rebuild_verifier.py", "--evidence-root", "."])


def mutate_acceptance_count(pristine: Path, template: Path, build: str):
    with tempfile.TemporaryDirectory(prefix="core0a-data-mutation-") as name:
        evidence = Path(name) / "evidence"
        shutil.copytree(pristine, evidence)
        baseline_process = run_aggregator(evidence, template)
        if baseline_process.returncode != 0:
            raise MutationHarnessError("preliminary aggregate is not passing")
        baseline = json.loads(baseline_process.stdout.strip().splitlines()[-1])
        report = {
            "CORE0A_gates": {
                gate_id: {"counts": gate["counts"], "status": gate["status"]}
                for gate_id, gate in baseline["gates"].items()
            }
        }
        report_path = evidence / "acceptance_report.yaml"
        report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
        original_text = report_path.read_text(encoding="utf-8")
        original = sha256(report_path)
        report["CORE0A_gates"]["non_vacuity_coverage"]["counts"][
            "N_positive_E0_satisfied_traces"
        ] = 0
        report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
        mutated = sha256(report_path)
        process = run_aggregator(evidence, template, report_path)
        report_path.write_text(original_text, encoding="utf-8")
        restored = sha256(report_path)
        return data_row(
            "acceptance_positive_e0_count_zero", build, "acceptance_report.yaml",
            "CORE0A_gates.non_vacuity_coverage.N_positive_E0_satisfied_traces",
            original, mutated, restored, process,
            "count mismatch:non_vacuity_coverage",
        )


def mutate_csv(pristine: Path, template: Path, build: str, mutation_id: str):
    with tempfile.TemporaryDirectory(prefix="core0a-data-mutation-") as name:
        evidence = Path(name) / "evidence"
        shutil.copytree(pristine, evidence)
        if mutation_id == "delete_finite_state_bound_check":
            filename = "bound_checks.csv"
            symbol = "one certified bound-check row"
            marker = "finite-state certificate/bound cardinality failure"
        else:
            filename = "dominance_tasksets.csv"
            symbol = "source_vector_hash"
            marker = "dominance uniqueness/vector invariant failure"
        path = evidence / filename
        original_bytes = path.read_bytes()
        original = sha256(path)
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = tuple(reader.fieldnames or ())
            rows = list(reader)
        if not rows:
            raise MutationHarnessError("{} has no mutation target".format(filename))
        if mutation_id == "delete_finite_state_bound_check":
            rows = rows[1:]
        else:
            rows[0]["source_vector_hash"] = "0" * 64
            if rows[0]["source_vector_hash"] == rows[0]["local_vector_hash"]:
                rows[0]["source_vector_hash"] = "f" * 64
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        mutated = sha256(path)
        refresh_manifest(evidence, filename)
        process = run_aggregator(evidence, template)
        path.write_bytes(original_bytes)
        restored = sha256(path)
        row = data_row(
            mutation_id, build, filename, symbol, original, mutated, restored,
            process, marker,
        )
        if row["detected"] != "true":
            raise MutationHarnessError(
                "{} was not detected: {}".format(mutation_id, process.stdout[-1000:])
            )
        return row


def mutate_positive_e0(pristine: Path, template: Path, build: str, mutation_id: str):
    """Mutate an actual certificate/bound evidence copy and retain transcripts."""
    with tempfile.TemporaryDirectory(prefix="core0a-positive-e0-mutation-") as name:
        evidence = Path(name) / "evidence"
        shutil.copytree(pristine, evidence)
        filename = "bound_checks.csv" if mutation_id == "delete_bound_check" else "release_energy_certificates.csv"
        path = evidence / filename
        original_bytes = path.read_bytes()
        original = sha256(path)
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = tuple(reader.fieldnames or ())
            rows = list(reader)
        if mutation_id == "delete_bound_check":
            rows = rows[1:]
            symbol = "certified bound-check row"
        else:
            index = next(i for i, row in enumerate(rows) if row["positive_E0"] == "true" and row["certificate_status"] == "SATISFIED")
            symbol = "positive-E0 certificate row"
            if mutation_id == "delete_release_certificate":
                del rows[index]
            elif mutation_id == "release_energy_below_e0":
                rows[index]["release_energy"] = str(int(rows[index]["E0"]) - 1)
            elif mutation_id == "certified_candidate_provisional":
                rows[index]["candidate_jointly_certified"] = "false"
            elif mutation_id == "break_certificate_job_fk":
                rows[index]["job_id"] += "-missing"
            else:
                raise MutationHarnessError("unknown positive-E0 mutation")
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        mutated = sha256(path)
        refresh_manifest(evidence, filename)
        process = run_aggregator(evidence, template)
        path.write_bytes(original_bytes)
        restored = sha256(path)
        row = data_row(
            mutation_id, build, filename, symbol, original, mutated, restored,
            process, "positive-E0 evidence invariant")
        if row["detected"] != "true":
            raise MutationHarnessError("{} was not detected: {}".format(mutation_id, process.stdout[-1000:]))
        return row


def run_producer(output: Path, build_identity: Path, mutations: Path,
                 random_instances: int):
    process = subprocess.run(
        [
            sys.executable, str(ROOT / "core0a_v9_3_evidence.py"),
            "--output", str(output), "--build-identity", str(build_identity),
            "--mutation-runs", str(mutations),
            "--random-envelope-instances", str(random_instances),
        ],
        cwd=ROOT, env=subprocess_env(ROOT), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900, check=False,
    )
    if process.returncode:
        raise MutationHarnessError(
            "evidence producer failed: {}".format((process.stdout + process.stderr)[-2000:])
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-identity", type=Path, required=True)
    parser.add_argument("--output-evidence", type=Path, required=True)
    parser.add_argument("--output-mutations", type=Path, required=True)
    parser.add_argument(
        "--template", type=Path,
        default=ROOT / "docs/ASAP_BLOCK_v1_3_12_机器合同静态冻结候选包/ASAP_BLOCK_acceptance_report_template_v1_3_12.yaml",
    )
    parser.add_argument("--random-envelope-instances", type=int, default=50_000)
    args = parser.parse_args()
    try:
        identity = json.loads(args.build_identity.read_text(encoding="utf-8"))
        build = identity["build_identity_hash"]
        if len(build) != 64:
            raise MutationHarnessError("invalid build identity hash")
        source_rows = [run_source_mutation(spec, build) for spec in SOURCE_MUTATIONS]
        with tempfile.TemporaryDirectory(prefix="core0a-mutation-bootstrap-") as name:
            temporary = Path(name)
            bootstrap_csv = temporary / "mutation_runs.csv"
            write_rows(bootstrap_csv, bootstrap_rows(source_rows, build))
            preliminary = temporary / "preliminary_evidence"
            run_producer(
                preliminary, args.build_identity.resolve(), bootstrap_csv,
                args.random_envelope_instances,
            )
            data_rows = [mutate_positive_e0(
                preliminary, args.template.resolve(), build, mutation_id)
                for mutation_id in (
                    "delete_release_certificate", "release_energy_below_e0",
                    "certified_candidate_provisional", "delete_bound_check",
                    "break_certificate_job_fk",
                )]
        rows = source_rows + data_rows
        if len(rows) != 15 or any(row["detected"] != "true" for row in rows):
            raise MutationHarnessError("not all fifteen mutations were detected")
        write_rows(args.output_mutations, rows)
        run_producer(
            args.output_evidence, args.build_identity.resolve(),
            args.output_mutations.resolve(), args.random_envelope_instances,
        )
        print(json.dumps({
            "status": "PASSED", "N_runs": len(rows), "N_survived": 0,
            "mutation_runs": str(args.output_mutations),
            "evidence_root": str(args.output_evidence),
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
