import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import audit_results
import execution_common as execution
import inspect_execution
import integration_smoke_common as smoke
import manifest_common as manifest


FIXED_TIMESTAMP = "2026-07-27T00:00:00+00:00"


class AuditFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="b4pe-i4c-")
        self.base = Path(self.temporary.name)
        self.output_root = (self.base / "outputs").resolve()
        self.output_root.mkdir()
        self.records = (self.base / "records").resolve()
        self.records.mkdir()
        self.generator = (self.base / "generator.py").resolve()
        self.generator.write_text("# fixture generator\n", encoding="utf-8")
        self.simulator = (self.base / "simulator").resolve()
        self.simulator.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.simulator.chmod(0o755)
        self.cases = []

    def cleanup(self):
        self.temporary.cleanup()

    def add_case(
        self,
        index,
        algorithm="ASAP-BLOCK",
        semantic_hash="a" * 64,
        deadline_miss=False,
        taskset_bytes=b"taskset: fixture-a\n",
        source_scale=0.5,
        system_suffix="",
        case_id=None,
        case_root=None,
        result_relpath=None,
        emax_j=1.0,
        current_energy_mj=500.0,
    ):
        scheduler = audit_results.ALGORITHM_CLI_MAPPING[algorithm]
        case_id = case_id or f"smoke-audit-{index:02d}-{algorithm.lower()}"
        case_root = (
            Path(case_root).resolve()
            if case_root is not None
            else (self.output_root / f"case-{index:02d}").resolve()
        )
        case_root.mkdir(parents=True, exist_ok=True)
        artifact_root = case_root / "integration-smoke/artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_root / "taskset.raw.yml"
        taskset_path = artifact_root / "taskset.yml"
        source_path = artifact_root / "source.json"
        system_path = artifact_root / "system.yml"
        raw_path.write_bytes(taskset_bytes)
        taskset_path.write_bytes(taskset_bytes)
        source = {
            "E0_j": emax_j / 2.0,
            "Emax_j": emax_j,
            "profile_id": "b4_pe_three_stage_v1",
            "source": {
                "kind": "scaled_piecewise",
                "scale_w": source_scale,
                "segments": [
                    {
                        "start_time_ms": 0,
                        "end_time_ms": 100,
                        "multiplier": 1.0,
                    }
                ],
            },
        }
        source_path.write_text(
            manifest.compact_json(source) + "\n",
            encoding="utf-8",
        )
        system_text = (
            "cpu_islands:\n"
            "  - name: fixture\n"
            "    numcpus: 4\n"
            "    kernel:\n"
            f"      scheduler: {scheduler}\n"
            "priority_energy:\n"
            "  enabled: true\n"
            "energy_management:\n"
            f"  initial_energy: {emax_j / 2.0}\n"
            f"  max_energy: {emax_j}\n"
            f"{system_suffix}"
        )
        system_path.write_text(system_text, encoding="utf-8")
        result_relpath = (
            result_relpath
            or f"integration-smoke/results/{case_id}.json"
        )
        record_path = (self.records / f"{index:02d}-{algorithm.lower()}.json").resolve()
        record = {
            "schema_version": "b4-pe-integration-smoke-v1",
            "record_type": "integration_smoke",
            "phase": "integration_smoke",
            "execution_scope": "single-real-case",
            "selected_case_count": 1,
            "campaign_started": False,
            "campaign_result_count": 0,
            "not_for_paper": True,
            "case_id": case_id,
            "algorithm": algorithm,
            "simulator_path": str(self.simulator),
            "output_root": str(case_root),
            "system_config_path": "integration-smoke/artifacts/system.yml",
            "taskset_path": "integration-smoke/artifacts/taskset.yml",
            "source_artifact_path": "integration-smoke/artifacts/source.json",
            "result_relpath": result_relpath,
            "timeout_seconds": 1,
            "retry_policy": {
                "initial_timeout_seconds": 1,
                "max_attempts": 2,
                "on_final_failure": "fail_closed",
                "retry_on": ["timeout"],
                "retry_timeout_seconds": 2,
            },
            "provenance": {
                "generator_path": str(self.generator),
                "generator_sha256": execution.file_sha256(self.generator),
                "generator_argv": [str(self.generator), "--seed", "1"],
                "taskset_raw_sha256": execution.file_sha256(raw_path),
                "taskset_semantic_hash": semantic_hash,
                "system_config_sha256": execution.file_sha256(system_path),
                "source_artifact_sha256": execution.file_sha256(source_path),
                "simulator_sha256": execution.file_sha256(self.simulator),
            },
        }
        record["command_argv"] = [
            record["simulator_path"],
            record["system_config_path"],
            record["taskset_path"],
            "100",
            "-t",
            record["result_relpath"],
            "--run-id",
            case_id,
            "--taskset-semantic-hash",
            semantic_hash,
        ]
        record_path.write_text(
            smoke.compact_json(record) + "\n",
            encoding="utf-8",
        )
        normalized = smoke.validate_integration_smoke_record(
            record_path
        )["records"][0]
        context = execution.build_context(
            record_path,
            case_root,
            self.simulator,
        )
        try:
            provenance = execution.build_provenance(normalized, context)
        finally:
            execution.close_context(context)
        state = execution.new_state(provenance)
        for descriptor, role in enumerate(
            execution.SNAPSHOT_ROLES,
            start=7,
        ):
            state[f"{role}_executed_proc_fd_path"] = (
                f"/proc/self/fd/{descriptor}"
            )

        events = [
            {
                "event_type": "arrival",
                "task_name": f"task_{task_index}",
                "time": 0,
                "current_energy_mJ": current_energy_mj,
                "total_consumed_mJ": 0.0,
                "total_harvested_mJ": 0.0,
            }
            for task_index in range(10)
        ]
        if deadline_miss:
            events.append(
                {
                    "event_type": "dline_miss",
                    "task_name": "task_0",
                    "time": 99,
                    "current_energy_mJ": 100.0,
                }
            )
        result = {
            "run_id": case_id,
            "configured_scheduler": scheduler,
            "taskset_semantic_hash": semantic_hash,
            "expected_simulation_horizon_ms": 100,
            "observed_simulation_end_ms": 100,
            "simulation_completed": True,
            "events": events,
        }
        result_path = case_root.joinpath(*Path(result_relpath).parts)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            manifest.compact_json(result) + "\n",
            encoding="utf-8",
        )
        staging_relative = (
            f".b4pe/attempt-results/{case_id}/attempt-0001-fixture"
        )
        staging_directory = case_root.joinpath(
            *Path(staging_relative).parts
        )
        staging_directory.mkdir(parents=True, exist_ok=True)
        staging_path = staging_directory / "trace.json"
        staging_path.write_bytes(result_path.read_bytes())
        log_directory = case_root / ".b4pe/logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        stdout_relative = f".b4pe/logs/{case_id}.stdout"
        stderr_relative = f".b4pe/logs/{case_id}.stderr"
        attempt_stdout_relative = f".b4pe/logs/{case_id}.attempt-1.stdout"
        attempt_stderr_relative = f".b4pe/logs/{case_id}.attempt-1.stderr"
        for relative in (
            stdout_relative,
            stderr_relative,
            attempt_stdout_relative,
            attempt_stderr_relative,
        ):
            case_root.joinpath(*Path(relative).parts).write_bytes(b"")
        result_sha = execution.file_sha256(result_path)
        stdout_sha = execution.file_sha256(
            case_root.joinpath(*Path(stdout_relative).parts)
        )
        stderr_sha = execution.file_sha256(
            case_root.joinpath(*Path(stderr_relative).parts)
        )
        publication = execution._new_publication()
        publication.update(
            {
                "publication_status": "committed",
                "staging_result_relpath": (
                    f"{staging_relative}/trace.json"
                ),
                "temporary_result_relpath": result_relpath,
                "temporary_stdout_relpath": stdout_relative,
                "temporary_stderr_relpath": stderr_relative,
                "final_result_relpath": result_relpath,
                "final_stdout_relpath": stdout_relative,
                "final_stderr_relpath": stderr_relative,
                "attempt_stdout_relpath": attempt_stdout_relative,
                "attempt_stderr_relpath": attempt_stderr_relative,
                "expected_result_sha256": result_sha,
                "observed_final_result_sha256": result_sha,
                "expected_stdout_sha256": stdout_sha,
                "expected_stderr_sha256": stderr_sha,
            }
        )
        attempt = {
            "attempt_index": 1,
            "timeout_seconds": 1,
            "started_at": "2026-07-27T00:00:00+00:00",
            "ended_at": "2026-07-27T00:00:01+00:00",
            "exit_code": 0,
            "termination_reason": "succeeded",
            "stdout_sha256": stdout_sha,
            "stderr_sha256": stderr_sha,
            "temporary_result_path": f"{staging_relative}/trace.json",
            "final_result_sha256": result_sha,
            "staging_directory_relpath": staging_relative,
            "staging_trace_basename": "trace.json",
            "staging_trace_sha256": result_sha,
            "publication": publication,
            "snapshot_execution": {},
        }
        state.update(
            {
                "attempt_count": 1,
                "attempts": [attempt],
                "current_status": "succeeded",
                "final_result_sha256": result_sha,
                "stdout_sha256": stdout_sha,
                "stderr_sha256": stderr_sha,
            }
        )
        state_path = case_root / ".b4pe/state" / f"{case_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            manifest.compact_json(state) + "\n",
            encoding="utf-8",
        )
        case = {
            "case_id": case_id,
            "root": case_root,
            "record_path": record_path,
            "record": record,
            "state_path": state_path,
            "result_path": result_path,
            "staging_path": staging_path,
        }
        self.cases.append(case)
        return case

    def read_state(self, case):
        return json.loads(case["state_path"].read_text(encoding="utf-8"))

    def write_state(self, case, state):
        case["state_path"].write_text(
            manifest.compact_json(state) + "\n",
            encoding="utf-8",
        )

    def replace_result(self, case, payload, close_sha=True):
        case["result_path"].write_bytes(payload)
        case["staging_path"].write_bytes(payload)
        if close_sha:
            state = self.read_state(case)
            digest = execution.file_sha256(case["result_path"])
            attempt = state["attempts"][0]
            publication = attempt["publication"]
            attempt["staging_trace_sha256"] = digest
            attempt["final_result_sha256"] = digest
            publication["expected_result_sha256"] = digest
            publication["observed_final_result_sha256"] = digest
            state["final_result_sha256"] = digest
            self.write_state(case, state)

    def make_failed_integrity_clean(self, case):
        case["result_path"].unlink()
        case["staging_path"].unlink()
        state = self.read_state(case)
        attempt = state["attempts"][0]
        publication = attempt["publication"]
        publication.update(
            {
                "publication_status": "none",
                "expected_result_sha256": None,
                "observed_final_result_sha256": None,
                "integrity_failure_reason": None,
                "expected_stdout_sha256": None,
                "expected_stderr_sha256": None,
            }
        )
        attempt.update(
            {
                "exit_code": 0,
                "termination_reason": "trace_missing",
                "staging_trace_sha256": None,
                "final_result_sha256": None,
            }
        )
        state.update(
            {
                "current_status": "failed",
                "final_result_sha256": None,
            }
        )
        self.write_state(case, state)

    def audit(self, strict=False):
        return audit_results.audit_output(
            self.output_root,
            expected_records=self.records,
            strict=strict,
            audit_timestamp=FIXED_TIMESTAMP,
        )

    def content_snapshot(self):
        return {
            path.relative_to(self.output_root).as_posix(): (
                path.read_bytes(),
                path.stat().st_mode,
            )
            for path in self.output_root.rglob("*")
            if path.is_file()
        }


class AuditResultsTests(unittest.TestCase):
    def setUp(self):
        self.fx = AuditFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_single_succeeded_case(self):
        self.fx.add_case(1)
        report = self.fx.audit()
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["succeeded_count"], 1)

    def test_legacy_trace_rounded_battery_cap_is_not_out_of_bounds(self):
        self.fx.add_case(
            1,
            emax_j=0.05700355590675622,
            current_energy_mj=57.0036,
        )
        report = self.fx.audit()
        self.assertTrue(report["overall_pass"])

        case = self.fx.cases[0]
        result = json.loads(case["result_path"].read_text(encoding="utf-8"))
        result["events"][0]["current_energy_mJ"] = 57.0037
        self.fx.replace_result(
            case,
            (manifest.compact_json(result) + "\n").encode(),
        )
        report = self.fx.audit()
        self.assertFalse(report["overall_pass"])
        self.assertIn(
            "battery_out_of_bounds",
            {issue["code"] for issue in report["per_case"][0]["issues"]},
        )

    def test_deadline_miss_is_only_scheduling_outcome(self):
        self.fx.add_case(1, deadline_miss=True)
        report = self.fx.audit()
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["infrastructure_failure_count"], 0)
        self.assertEqual(report["scheduling_outcome_count"], 1)

    def test_failed_integrity_clean_case_is_infrastructure_failure(self):
        case = self.fx.add_case(1)
        self.fx.make_failed_integrity_clean(case)
        inspection = inspect_execution.inspect_output(case["root"])
        self.assertFalse(
            inspect_execution.inspection_has_integrity_errors(inspection)
        )

        report = self.fx.audit()
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["infrastructure_failure_count"], 1)
        self.assertFalse(report["overall_pass"])
        self.assertIn(
            "case_not_succeeded",
            {issue["code"] for issue in report["per_case"][0]["issues"]},
        )

        report_path = (self.fx.base / "failed-report.json").resolve()
        self.assertEqual(
            audit_results.main(
                [
                    "--output-root",
                    str(self.fx.output_root),
                    "--expected-records",
                    str(self.fx.records),
                    "--report",
                    str(report_path),
                ]
            ),
            1,
        )

    def test_non_strict_legal_incomplete_group_passes(self):
        self.fx.add_case(1)
        report = self.fx.audit(strict=False)
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["pairing_group_count"], 1)
        self.assertTrue(report["incomplete_algorithm_groups"])
        self.assertEqual(
            report["per_pairing_group"][0]["algorithm_coverage"],
            ["ASAP-BLOCK"],
        )

    def test_missing_result(self):
        case = self.fx.add_case(1)
        case["result_path"].unlink()
        report = self.fx.audit()
        self.assertFalse(report["overall_pass"])
        self.assertEqual(report["missing_results"], [case["case_id"]])

    def test_corrupt_json_result(self):
        case = self.fx.add_case(1)
        self.fx.replace_result(case, b"{bad json}\n")
        report = self.fx.audit()
        self.assertEqual(report["corrupt_results"], [case["case_id"]])
        self.assertGreater(report["infrastructure_failure_count"], 0)

    def test_result_sha_mismatch(self):
        case = self.fx.add_case(1)
        case["result_path"].write_text("changed\n", encoding="utf-8")
        report = self.fx.audit()
        self.assertTrue(report["sha_mismatches"])
        self.assertFalse(report["overall_pass"])

    def test_provenance_mismatch(self):
        case = self.fx.add_case(1)
        state = self.fx.read_state(case)
        state["source_observed_original_sha256"] = "f" * 64
        self.fx.write_state(case, state)
        report = self.fx.audit()
        self.assertEqual(
            report["provenance_errors"],
            [f"{case['root']}:1"],
        )

    def test_duplicate_case_id(self):
        self.fx.add_case(1, case_id="smoke-audit-duplicate")
        self.fx.add_case(2, case_id="smoke-audit-duplicate")
        report = self.fx.audit()
        self.assertEqual(
            report["duplicate_case_ids"],
            ["smoke-audit-duplicate"],
        )
        self.assertGreater(report["audit_failure_count"], 0)

    def test_duplicate_result_path(self):
        shared = (self.output_root / "shared").resolve()
        self.fx.add_case(
            1,
            case_root=shared,
            result_relpath="integration-smoke/results/shared.json",
        )
        self.fx.add_case(
            2,
            algorithm="ASAP-NONBLOCK",
            case_root=shared,
            result_relpath="integration-smoke/results/shared.json",
        )
        report = self.fx.audit()
        self.assertEqual(len(report["duplicate_result_paths"]), 1)

    @property
    def output_root(self):
        return self.fx.output_root

    def test_unknown_scheduler(self):
        case = self.fx.add_case(1)
        document = json.loads(case["result_path"].read_text(encoding="utf-8"))
        document["configured_scheduler"] = "unknown_scheduler"
        self.fx.replace_result(
            case,
            (manifest.compact_json(document) + "\n").encode(),
        )
        for strict in (False, True):
            with self.subTest(strict=strict):
                report = self.fx.audit(strict=strict)
                self.assertFalse(report["overall_pass"])
                self.assertEqual(
                    report["unknown_schedulers"],
                    ["unknown_scheduler"],
                )

    def test_non_strict_unknown_algorithm_fails(self):
        case = self.fx.add_case(1)
        state = self.fx.read_state(case)
        state["algorithm"] = "UNKNOWN-ALGORITHM"
        self.fx.write_state(case, state)
        report = self.fx.audit(strict=False)
        self.assertFalse(report["overall_pass"])
        self.assertIn(
            "unknown_algorithm",
            {issue["code"] for issue in report["per_case"][0]["issues"]},
        )

    def test_unfinished_publication(self):
        case = self.fx.add_case(1)
        state = self.fx.read_state(case)
        state["current_status"] = "running"
        state["attempts"][0]["publication"]["publication_status"] = "prepared"
        self.fx.write_state(case, state)
        report = self.fx.audit()
        self.assertEqual(
            report["unfinished_publications"],
            [f"{case['root']}:1"],
        )

    def test_orphan_result(self):
        self.fx.add_case(1)
        orphan = (
            self.fx.cases[0]["result_path"].parent / "orphan.json"
        )
        orphan.write_text("{}\n", encoding="utf-8")
        report = self.fx.audit()
        self.assertEqual(report["orphan_artifacts"], [str(orphan)])

    def test_pairing_taskset_sha_mismatch(self):
        self.fx.add_case(1, taskset_bytes=b"taskset: one\n")
        self.fx.add_case(
            2,
            algorithm="ASAP-NONBLOCK",
            taskset_bytes=b"taskset: two\n",
        )
        report = self.fx.audit()
        fields = {item["field"] for item in report["pairing_mismatches"]}
        self.assertIn("raw_taskset_sha256", fields)
        self.assertIn("taskset_snapshot_sha256", fields)

    def test_pairing_source_sha_mismatch(self):
        self.fx.add_case(1, source_scale=0.5)
        self.fx.add_case(
            2,
            algorithm="ASAP-NONBLOCK",
            source_scale=0.6,
        )
        report = self.fx.audit()
        fields = {item["field"] for item in report["pairing_mismatches"]}
        self.assertIn("source_snapshot_sha256", fields)

    def test_pairing_system_non_scheduler_mismatch(self):
        self.fx.add_case(1)
        self.fx.add_case(
            2,
            algorithm="ASAP-NONBLOCK",
            system_suffix="extra_field: changed\n",
        )
        report = self.fx.audit()
        fields = {item["field"] for item in report["pairing_mismatches"]}
        self.assertIn("normalized_system", fields)

    def test_strict_mode_requires_nine_algorithms(self):
        self.fx.add_case(1)
        report = self.fx.audit(strict=True)
        self.assertFalse(report["overall_pass"])
        self.assertEqual(len(report["incomplete_algorithm_groups"]), 1)

    def test_strict_extra_unknown_algorithm_and_scheduler_fails(self):
        for index, algorithm in enumerate(audit_results.ALGORITHMS, 1):
            self.fx.add_case(index, algorithm=algorithm)
        extra = self.fx.add_case(10)
        state = self.fx.read_state(extra)
        state["algorithm"] = "UNKNOWN-ALGORITHM"
        self.fx.write_state(extra, state)
        result = json.loads(
            extra["result_path"].read_text(encoding="utf-8")
        )
        result["configured_scheduler"] = "unknown_scheduler"
        self.fx.replace_result(
            extra,
            (manifest.compact_json(result) + "\n").encode(),
        )

        report = self.fx.audit(strict=True)
        self.assertFalse(report["overall_pass"])
        group_codes = {
            issue["code"]
            for issue in report["per_pairing_group"][0]["issues"]
        }
        self.assertIn("unknown_algorithm", group_codes)
        self.assertIn("unknown_scheduler", group_codes)
        self.assertIn("scheduler_coverage_mismatch", group_codes)

        report_path = (self.fx.base / "strict-unknown-report.json").resolve()
        self.assertEqual(
            audit_results.main(
                [
                    "--output-root",
                    str(self.fx.output_root),
                    "--expected-records",
                    str(self.fx.records),
                    "--report",
                    str(report_path),
                    "--strict",
                ]
            ),
            1,
        )

    def test_strict_duplicate_algorithm_fails(self):
        self.fx.add_case(1)
        self.fx.add_case(2)
        report = self.fx.audit(strict=True)
        self.assertFalse(report["overall_pass"])
        self.assertIn(
            "duplicate_algorithm",
            {
                issue["code"]
                for issue in report["per_pairing_group"][0]["issues"]
            },
        )

    def test_complete_nine_algorithm_pairing_passes(self):
        for index, algorithm in enumerate(audit_results.ALGORITHMS, 1):
            self.fx.add_case(index, algorithm=algorithm)
        report = self.fx.audit(strict=True)
        self.assertTrue(report["overall_pass"])
        self.assertEqual(report["pairing_group_count"], 1)
        self.assertEqual(report["pairing_mismatches"], [])

    def test_deterministic_sorting(self):
        for index, algorithm in enumerate(
            reversed(audit_results.ALGORITHMS),
            1,
        ):
            self.fx.add_case(index, algorithm=algorithm)
        first = self.fx.audit(strict=True)
        second = self.fx.audit(strict=True)
        self.assertEqual(first, second)
        case_ids = [case["case_id"] for case in first["per_case"]]
        self.assertEqual(case_ids, sorted(case_ids))

    def test_audit_is_read_only(self):
        self.fx.add_case(1)
        before = self.fx.content_snapshot()
        self.fx.audit()
        self.assertEqual(before, self.fx.content_snapshot())

    def test_cli_exit_codes_zero_one_two(self):
        self.fx.add_case(1)
        report_zero = (self.fx.base / "report-zero.json").resolve()
        self.assertEqual(
            audit_results.main(
                [
                    "--output-root",
                    str(self.fx.output_root),
                    "--expected-records",
                    str(self.fx.records),
                    "--report",
                    str(report_zero),
                ]
            ),
            0,
        )
        orphan = self.fx.cases[0]["result_path"].parent / "orphan.json"
        orphan.write_text("{}\n", encoding="utf-8")
        report_one = (self.fx.base / "report-one.json").resolve()
        self.assertEqual(
            audit_results.main(
                [
                    "--output-root",
                    str(self.fx.output_root),
                    "--expected-records",
                    str(self.fx.records),
                    "--report",
                    str(report_one),
                ]
            ),
            1,
        )
        self.assertEqual(
            audit_results.main(
                [
                    "--output-root",
                    "relative",
                    "--report",
                    str((self.fx.base / "invalid.json").resolve()),
                ]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
