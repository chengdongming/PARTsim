import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = B4_DIR.parents[1]
sys.path.insert(0, str(B4_DIR))

import execute_manifest
import execution_common as execution
import manifest_common as manifest
from test_execution_success import ExecutionFixture


class ExecutionProtocolTests(unittest.TestCase):
    def test_protocol_is_self_consistent(self):
        protocol = execution.load_execution_protocol()
        self.assertEqual(protocol["schema_version"], 1)
        self.assertEqual(protocol["state_schema_version"], 1)

    def test_protocol_sha_references_are_live(self):
        protocol = execution.PROTOCOL
        self.assertEqual(
            protocol["manifest_protocol_sha256"],
            execution.file_sha256(manifest.MANIFEST_PROTOCOL_PATH),
        )
        self.assertEqual(
            protocol["identity_protocol_sha256"],
            execution.file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        )

    def test_state_machine_has_required_states(self):
        self.assertEqual(
            execution.PROTOCOL["states"],
            ["planned", "running", "succeeded", "failed", "timed_out", "interrupted"],
        )

    def test_succeeded_has_no_outgoing_transition(self):
        self.assertEqual(execution.PROTOCOL["state_transitions"]["succeeded"], [])
        self.assertIn("running", execution.PROTOCOL["state_transitions"]["failed"])
        self.assertIn("running", execution.PROTOCOL["state_transitions"]["timed_out"])

    def test_summary_fields_are_frozen(self):
        summary = execution.new_summary("a" * 64, 3)
        self.assertEqual(list(summary), execution.PROTOCOL["summary_fields"])
        self.assertEqual(summary["selected_cases"], 3)

    def test_without_execute_refuses_before_process_start(self):
        stderr = io.StringIO()
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            with contextlib.redirect_stderr(stderr):
                status = execute_manifest.main(
                    [
                        "--manifest", "/does/not/exist",
                        "--output-root", "/tmp/no-create",
                        "--simulator-binary", "/does/not/exist",
                    ]
                )
        self.assertEqual(status, 2)
        self.assertIn("explicit --execute", stderr.getvalue())
        popen.assert_not_called()

    def test_relative_simulator_path_fails(self):
        fx = ExecutionFixture()
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = execute_manifest.main(
                    [
                        "--manifest", str(fx.manifest_path),
                        "--output-root", str(fx.output_root),
                        "--simulator-binary", "relative/simulator",
                        "--execute", "--limit", "0",
                    ]
                )
            self.assertEqual(status, 1)
            self.assertIn("must be absolute", stderr.getvalue())
        finally:
            fx.cleanup()

    def test_missing_simulator_fails(self):
        fx = ExecutionFixture()
        try:
            fx.simulator.unlink()
            self.assertEqual(fx.run_cli()[0], 1)
        finally:
            fx.cleanup()

    def test_non_executable_simulator_fails(self):
        fx = ExecutionFixture()
        try:
            fx.simulator.chmod(0o644)
            self.assertEqual(fx.run_cli()[0], 1)
        finally:
            fx.cleanup()

    def test_output_root_inside_repository_fails(self):
        fx = ExecutionFixture()
        try:
            candidate = B4_DIR / "forbidden-i4b-output"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = execute_manifest.main(
                    [
                        "--manifest", str(fx.manifest_path),
                        "--output-root", str(candidate),
                        "--simulator-binary", str(fx.simulator),
                        "--execute", "--limit", "0",
                    ]
                )
            self.assertEqual(status, 1)
            self.assertFalse(candidate.exists())
        finally:
            fx.cleanup()

    def test_no_parallel_or_force_cli(self):
        help_text = execute_manifest.build_parser().format_help()
        self.assertNotIn("--workers", help_text)
        self.assertNotIn("--parallel", help_text)
        self.assertNotIn("--force", help_text)

    def test_invalid_manifest_prevents_process_start(self):
        fx = ExecutionFixture()
        try:
            lines = fx.manifest_path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            first["algorithm"] = "UNKNOWN"
            lines[0] = manifest.compact_json(first)
            fx.manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with mock.patch.object(execution.subprocess, "Popen") as popen:
                status = fx.run_cli()[0]
            self.assertEqual(status, 1)
            popen.assert_not_called()
        finally:
            fx.cleanup()

    def test_execution_protocol_does_not_modify_manifest_protocol(self):
        self.assertEqual(
            execution.PROTOCOL["manifest_protocol_ref"], "manifest_protocol_v1.json"
        )
        self.assertNotIn("execution", manifest.PROTOCOL)

    def test_publication_transaction_contract_is_explicit(self):
        self.assertEqual(
            execution.PROTOCOL["publication_rules"]["statuses"],
            execution.PUBLICATION_STATUSES,
        )
        self.assertEqual(
            execution.PROTOCOL["publication_rules"]["commit_order"][0],
            "result_parent_temporary_fsync",
        )
        self.assertEqual(
            execution.PROTOCOL["dirfd_safety_rules"]["replace"],
            "same_parent_src_dir_fd_and_dst_dir_fd",
        )
        self.assertEqual(
            execution.PROTOCOL["publication_rules"]["staging_retention"],
            "retain_attempt_trace_evidence_after_success",
        )
        self.assertIn(
            "result_post_replace_reopen_verify",
            execution.PROTOCOL["publication_rules"]["commit_order"],
        )
        self.assertIn(
            "stable_fstat_and_repeated_sha256",
            execution.PROTOCOL["result_integrity_rules"]["after_replace"],
        )

    def test_production_argv_and_i4b2_boundary_are_explicit(self):
        rules = execution.PROTOCOL["subprocess_rules"]
        self.assertFalse(rules["test_argument_hook"])
        self.assertFalse(rules["unit_test_campaign_execution"])
        self.assertEqual(
            rules["taskset_semantic_hash_source"],
            "upstream_manifest_command_bridge_i4b2",
        )
        self.assertEqual(
            rules["real_rtsim_end_to_end_validation"],
            "i4b2_first_gate",
        )
        self.assertEqual(
            execution.PROTOCOL["inspection_rules"][
                "cli_integrity_error_exit_code"
            ],
            1,
        )

    def test_dirfd_and_snapshot_contracts_are_explicit(self):
        self.assertIn("O_NOFOLLOW", execution.PROTOCOL["dirfd_safety_rules"]["component_open"])
        self.assertEqual(
            execution.PROTOCOL["input_snapshot_rules"]["execution_source"],
            "content_addressed_read_only_snapshots_via_inherited_final_file_descriptors",
        )
        self.assertEqual(
            execution.PROTOCOL["input_snapshot_rules"]["snapshot_proc_path"],
            "/proc/self/fd/<snapshot-file-fd>",
        )
        self.assertFalse(
            execution.PROTOCOL["input_snapshot_rules"]["proc_fd_fallback"]
        )
        self.assertFalse(
            execution.PROTOCOL["input_snapshot_rules"][
                "snapshot_directory_descriptors_inherited"
            ]
        )
        self.assertFalse(
            execution.PROTOCOL["subprocess_rules"][
                "output_root_rootfd_inherited"
            ]
        )
        self.assertFalse(
            execution.PROTOCOL["subprocess_rules"]["trace_target_precreated"]
        )
        self.assertEqual(
            execution.PROTOCOL["trace_staging_rules"][
                "target_basename_by_result_suffix"
            ],
            {".json": "trace.json", ".txt": "trace.txt"},
        )
        self.assertFalse(
            execution.PROTOCOL["trace_staging_rules"]["orphan_adoption"]
        )

    def test_summary_contract_is_content_addressed(self):
        self.assertEqual(
            execution.PROTOCOL["summary_rules"]["path"],
            ".b4pe/summaries/<sha256>.json",
        )
        self.assertFalse(execution.PROTOCOL["summary_rules"]["latest_pointer"])


if __name__ == "__main__":
    unittest.main()
