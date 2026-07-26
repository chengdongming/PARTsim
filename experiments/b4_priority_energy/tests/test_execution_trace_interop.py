import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import inspect_execution
from test_execution_success import ExecutionFixture


class ExecutionTraceInteropTests(unittest.TestCase):
    def setUp(self):
        self.fx = ExecutionFixture()

    def tearDown(self):
        self.fx.cleanup()

    def record_with_suffix(self, suffix):
        record = self.fx.direct_record(retry_timeout=1)
        original = record["result_relpath"]
        replacement = str(Path(original).with_suffix(suffix))
        record["result_relpath"] = replacement
        record["command_argv"] = [
            replacement if item == original else item
            for item in record["command_argv"]
        ]
        return record

    def execute(self, record, config, **kwargs):
        self.fx.write_inputs(config, record=record)
        context = self.fx.context()
        try:
            return execution.execute_records([record], context, **kwargs)
        finally:
            execution.close_context(context)

    def test_rtsim_contract_txt_staging_trace_succeeds(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(
            record,
            {"mode": "rtsim-trace-contract", "result_text": "txt trace\n"},
        )
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(
            self.fx.path(record["result_relpath"]).read_text(), "txt trace\n"
        )
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["staging_trace_basename"],
            "trace.txt",
        )

    def test_rtsim_contract_json_staging_trace_succeeds(self):
        record = self.record_with_suffix(".json")
        payload = json.dumps({"trace": "valid"}, sort_keys=True) + "\n"
        summary = self.execute(
            record,
            {"mode": "rtsim-trace-contract", "result_text": payload},
        )
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(
            json.loads(self.fx.path(record["result_relpath"]).read_text()),
            {"trace": "valid"},
        )
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["staging_trace_basename"],
            "trace.json",
        )

    def test_illegal_tmp_suffix_fails_before_popen(self):
        record = self.record_with_suffix(".tmp")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_trace_target_is_absent_at_before_popen_hook(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        observed = []

        def inspect_target(_record, context):
            staging = context["active_attempt_staging"]
            with self.assertRaises(FileNotFoundError):
                os.stat(
                    staging["trace_basename"],
                    dir_fd=staging["directory_fd"],
                    follow_symlinks=False,
                )
            observed.append(staging["trace_relpath"])

        with mock.patch.object(
            execution, "_before_popen_hook", side_effect=inspect_target
        ):
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(len(observed), 1)

    def test_preexisting_trace_target_fails_with_zero_popen(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)

        def create_target(_record, context):
            staging = context["active_attempt_staging"]
            descriptor = os.open(
                staging["trace_basename"],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=staging["directory_fd"],
            )
            os.close(descriptor)

        with mock.patch.object(
            execution, "_before_popen_hook", side_effect=create_target
        ), mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()
        trace = self.fx.path(
            self.fx.state(record)["attempts"][0]["temporary_result_path"]
        )
        self.assertTrue(trace.is_file())

    def test_success_without_trace_is_invalid_output(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(record, {"mode": "missing_result"})
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["termination_reason"],
            "missing_result",
        )

    def test_empty_trace_is_invalid_output(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(record, {"mode": "empty_result"})
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["termination_reason"],
            "empty_result",
        )

    def test_symlink_trace_is_invalid_output(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(record, {"mode": "symlink_result"})
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["termination_reason"],
            "invalid_result",
        )

    def test_directory_trace_is_invalid_output(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(record, {"mode": "directory_result"})
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["termination_reason"],
            "invalid_result",
        )

    def test_extra_staging_target_is_invalid_output(self):
        record = self.record_with_suffix(".txt")
        summary = self.execute(record, {"mode": "extra_result"})
        self.assertEqual(summary["failed"], 1)
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["multiple_staging_trace_targets"], 1)
        self.assertEqual(report["failed_staging_evidence"], 1)

    def test_inspector_reports_staging_trace_sha_mismatch(self):
        record = self.fx.direct_record(initial_timeout=0.15, max_attempts=1)
        self.assertEqual(
            self.execute(
                record,
                {"mode": "result_then_child_hang", "sleep_seconds": 10},
            )["timed_out"],
            1,
        )
        state_path = self.fx.path(f".b4pe/state/{record['case_id']}.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["attempts"][0]["staging_trace_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["staging_trace_sha_mismatches"], 1)

    def test_inspector_reports_prepared_staging_publication_sha_mismatch(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs(
            {"mode": "rtsim-trace-contract"}, record=record
        )
        context = self.fx.context()
        real_replace = execution._replace_at

        def stop_before_result(
            ctx,
            temporary,
            final,
            allow_existing=False,
            expected_sha256=None,
            post_replace_verifier=None,
        ):
            if final == record["result_relpath"]:
                raise OSError("preserve prepared publication")
            return real_replace(
                ctx,
                temporary,
                final,
                allow_existing,
                expected_sha256,
                post_replace_verifier,
            )

        with mock.patch.object(
            execution, "_replace_at", side_effect=stop_before_result
        ):
            summary = execution.execute_records([record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        state_path = self.fx.path(
            f".b4pe/state/{record['case_id']}.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        attempt = state["attempts"][0]
        self.assertEqual(
            attempt["publication"]["publication_status"], "prepared"
        )
        self.assertNotEqual(
            attempt["publication"]["expected_result_sha256"], "0" * 64
        )
        attempt["staging_trace_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        before = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        report = inspect_execution.inspect_output(self.fx.output_root)
        after = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(report["staging_trace_sha_mismatches"], 1)
        self.assertEqual(report["publication_integrity_errors"], 1)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = inspect_execution.main(
                ["--output-root", str(self.fx.output_root), "--json"]
            )
        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(stdout.getvalue())["publication_integrity_errors"],
            1,
        )
        self.assertEqual(before, after)

    def test_inspector_clean_succeeded_case_returns_zero_json(self):
        record = self.record_with_suffix(".txt")
        self.assertEqual(
            self.execute(record, {"mode": "rtsim-trace-contract"})[
                "succeeded"
            ],
            1,
        )
        before = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = inspect_execution.main(
                ["--output-root", str(self.fx.output_root), "--json"]
            )
        after = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["state_count"], 1)
        self.assertEqual(before, after)

    def test_inspector_reports_illegal_staging_suffix(self):
        record = self.fx.direct_record(initial_timeout=0.15, max_attempts=1)
        self.assertEqual(
            self.execute(record, {"mode": "sleep", "sleep_seconds": 10})[
                "timed_out"
            ],
            1,
        )
        state_path = self.fx.path(f".b4pe/state/{record['case_id']}.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        attempt = state["attempts"][0]
        attempt["staging_trace_basename"] = "trace.tmp"
        attempt["temporary_result_path"] = (
            f"{attempt['staging_directory_relpath']}/trace.tmp"
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["illegal_staging_suffixes"], 1)

    def test_inspector_reports_missing_prepared_staging_trace(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        context = self.fx.context()
        real_replace = execution._replace_at

        def stop_before_result(
            ctx,
            temporary,
            final,
            allow_existing=False,
            expected_sha256=None,
            post_replace_verifier=None,
        ):
            if final == record["result_relpath"]:
                raise OSError("preserve prepared staging trace")
            return real_replace(
                ctx,
                temporary,
                final,
                allow_existing,
                expected_sha256,
                post_replace_verifier,
            )

        with mock.patch.object(
            execution, "_replace_at", side_effect=stop_before_result
        ):
            summary = execution.execute_records([record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        attempt = self.fx.state(record)["attempts"][0]
        self.fx.path(attempt["temporary_result_path"]).unlink()
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["staging_trace_missing"], 1)

    def test_attempt_directory_is_private_and_success_trace_is_retained(self):
        record = self.record_with_suffix(".txt")
        self.assertEqual(
            self.execute(record, {"mode": "rtsim-trace-contract"})[
                "succeeded"
            ],
            1,
        )
        attempt = self.fx.state(record)["attempts"][0]
        directory = self.fx.path(attempt["staging_directory_relpath"])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(
            [path.name for path in directory.iterdir()], ["trace.txt"]
        )
        self.assertEqual(
            self.fx.path(attempt["temporary_result_path"]).read_bytes(),
            self.fx.path(record["result_relpath"]).read_bytes(),
        )
        publication = attempt["publication"]
        self.assertEqual(
            Path(publication["temporary_result_relpath"]).parent,
            Path(publication["final_result_relpath"]).parent,
        )
        self.assertEqual(
            publication["staging_result_relpath"],
            attempt["temporary_result_path"],
        )
        self.assertFalse(
            self.fx.path(publication["temporary_result_relpath"]).exists()
        )
        self.assertTrue(self.fx.path(record["result_relpath"]).is_file())
        self.assertEqual(
            publication["observed_final_result_sha256"],
            publication["expected_result_sha256"],
        )
        self.assertIsNone(publication["integrity_failure_reason"])

    def _assert_final_publication_tamper_fails_closed(self, mutation):
        record = self.record_with_suffix(".txt")
        original = b"original-publication\n"
        tampered = b"tampered-publication\n"
        self.fx.write_inputs(
            {
                "mode": "rtsim-trace-contract",
                "result_text": original.decode("ascii"),
            },
            record=record,
        )
        injected = {"done": False}

        def before_replace(_context, temporary, final, _parent_fd):
            if final != record["result_relpath"] or injected["done"]:
                return
            if mutation not in {"temporary_content", "temporary_name"}:
                return
            temporary_path = self.fx.path(temporary)
            if mutation == "temporary_content":
                temporary_path.write_bytes(tampered)
            else:
                replacement = temporary_path.with_name("replacement-result")
                replacement.write_bytes(tampered)
                os.replace(replacement, temporary_path)
            injected["done"] = True

        def publication_hook(stage, _record, _context, _attempt, publication):
            target_stage = {
                "final_after_replace": "after_result_replace_before_final_reopen",
                "final_during_hash": "after_final_first_hash",
            }.get(mutation)
            if stage != target_stage or injected["done"]:
                return
            self.fx.path(publication["final_result_relpath"]).write_bytes(
                tampered
            )
            injected["done"] = True

        context = self.fx.context()
        try:
            with mock.patch.object(
                execution, "_before_replace_hook", side_effect=before_replace
            ), mock.patch.object(
                execution,
                "_publication_integrity_hook",
                side_effect=publication_hook,
            ):
                summary = execution.execute_records([record], context)
        finally:
            execution.close_context(context)
        self.assertTrue(injected["done"])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["infrastructure_errors"], 0)
        state = self.fx.state(record)
        attempt = state["attempts"][0]
        publication = attempt["publication"]
        self.assertEqual(state["current_status"], "failed")
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(attempt["attempt_index"], 1)
        self.assertEqual(attempt["termination_reason"], "trace_integrity_error")
        self.assertEqual(publication["publication_status"], "prepared")
        self.assertEqual(
            publication["expected_result_sha256"],
            hashlib.sha256(original).hexdigest(),
        )
        self.assertIsNotNone(publication["integrity_failure_reason"])
        self.assertTrue(self.fx.path(attempt["temporary_result_path"]).is_file())
        final = self.fx.path(record["result_relpath"])
        if mutation == "temporary_name":
            self.assertFalse(final.exists())
            self.assertIsNone(publication["observed_final_result_sha256"])
            self.assertTrue(
                self.fx.path(publication["temporary_result_relpath"]).is_file()
            )
        else:
            self.assertTrue(final.is_file())
            self.assertEqual(final.read_bytes(), tampered)
            self.assertEqual(
                publication["observed_final_result_sha256"],
                hashlib.sha256(tampered).hexdigest(),
            )

    def test_temporary_content_tamper_after_last_check_fails_closed(self):
        self._assert_final_publication_tamper_fails_closed(
            "temporary_content"
        )

    def test_temporary_name_replacement_after_last_check_fails_closed(self):
        self._assert_final_publication_tamper_fails_closed("temporary_name")

    def test_final_tamper_after_replace_before_reopen_fails_closed(self):
        self._assert_final_publication_tamper_fails_closed(
            "final_after_replace"
        )

    def test_final_change_during_repeated_hash_fails_closed(self):
        self._assert_final_publication_tamper_fails_closed("final_during_hash")

    def _assert_post_exit_tamper_fails_closed(self, mutation):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs(
            {"mode": "rtsim-trace-contract", "result_text": "original\n"},
            record=record,
        )
        injected = {"done": False}

        def mutate(stage, _record, _context, attempt, _publication):
            if stage != "after_initial_validation" or injected["done"]:
                return
            injected["done"] = True
            trace = self.fx.path(attempt["temporary_result_path"])
            original = trace.read_bytes()
            if mutation == "content":
                trace.write_bytes(b"changed-content\n")
            elif mutation == "inode":
                replacement = trace.with_name("replacement.trace")
                replacement.write_bytes(original)
                os.replace(replacement, trace)
            elif mutation == "sha":
                trace.write_bytes(b"changed-sha\n")
            elif mutation == "type":
                trace.unlink()
                trace.mkdir()
            elif mutation == "missing":
                trace.unlink()
            else:
                self.fail(f"unknown mutation: {mutation}")

        with mock.patch.object(
            execution, "_publication_integrity_hook", side_effect=mutate
        ):
            summary = execution.execute_records(
                [record], self.fx.context()
            )
        self.assertTrue(injected["done"])
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["infrastructure_errors"], 0)
        state = self.fx.state(record)
        attempt = state["attempts"][0]
        self.assertEqual(state["current_status"], "failed")
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(attempt["exit_code"], 0)
        self.assertEqual(
            attempt["termination_reason"], "trace_integrity_error"
        )
        self.assertEqual(
            attempt["publication"]["publication_status"], "none"
        )
        self.assertFalse(self.fx.path(record["result_relpath"]).exists())
        self.assertFalse(
            self.fx.path(
                attempt["publication"]["temporary_result_relpath"]
            ).exists()
        )

    def test_post_exit_staging_content_change_persists_failed_state(self):
        self._assert_post_exit_tamper_fails_closed("content")

    def test_post_exit_staging_inode_change_persists_failed_state(self):
        self._assert_post_exit_tamper_fails_closed("inode")

    def test_post_exit_staging_sha_change_persists_failed_state(self):
        self._assert_post_exit_tamper_fails_closed("sha")

    def test_post_exit_staging_type_change_persists_failed_state(self):
        self._assert_post_exit_tamper_fails_closed("type")

    def test_post_exit_staging_missing_persists_failed_state(self):
        self._assert_post_exit_tamper_fails_closed("missing")

    def test_pass_fds_are_four_inputs_plus_attempt_directory(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        real_popen = execution.subprocess.Popen
        calls = []

        def inspect_popen(argv, **kwargs):
            trace_path = argv[argv.index("-t") + 1]
            self.assertRegex(trace_path, r"^/proc/self/fd/[0-9]+/trace\.txt$")
            with self.assertRaises(FileNotFoundError):
                os.stat(trace_path, follow_symlinks=False)
            attempt_fd = int(trace_path.split("/")[4])
            self.assertTrue(stat.S_ISDIR(os.fstat(attempt_fd).st_mode))
            self.assertEqual(kwargs["pass_fds"][-1], attempt_fd)
            self.assertEqual(len(kwargs["pass_fds"]), 5)
            calls.append((list(argv), dict(kwargs)))
            return real_popen(argv, **kwargs)

        context = self.fx.context()
        with mock.patch.object(
            execution.subprocess, "Popen", side_effect=inspect_popen
        ):
            summary = execution.execute_records([record], context)
        self.assertEqual(summary["succeeded"], 1)
        self.assertNotIn(context["root_fd"], calls[0][1]["pass_fds"])

    def test_retry_uses_distinct_staging_directories(self):
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=1)
        summary = self.execute(
            record,
            {
                "mode": "first_timeout_then_success",
                "sleep_seconds": 10,
                "result_text": "retry\n",
            },
        )
        self.assertEqual(summary["succeeded"], 1)
        attempts = self.fx.state(record)["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(
            attempts[0]["staging_directory_relpath"],
            attempts[1]["staging_directory_relpath"],
        )
        self.assertNotEqual(
            attempts[0]["temporary_result_path"],
            attempts[1]["temporary_result_path"],
        )

    def test_retry_targets_are_absent_on_both_popen_calls(self):
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=1)
        self.fx.write_inputs(
            {"mode": "first_timeout_then_success", "sleep_seconds": 10},
            record=record,
        )
        real_popen = execution.subprocess.Popen
        observed = []

        def inspect_popen(argv, **kwargs):
            trace_path = argv[argv.index("-t") + 1]
            observed.append(os.readlink(str(Path(trace_path).parent)))
            self.assertFalse(os.path.lexists(trace_path))
            return real_popen(argv, **kwargs)

        with mock.patch.object(
            execution.subprocess, "Popen", side_effect=inspect_popen
        ):
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(len(observed), 2)
        self.assertNotEqual(observed[0], observed[1])

    def test_timeout_staging_trace_is_retained_not_published(self):
        record = self.fx.direct_record(initial_timeout=0.15, max_attempts=1)
        summary = self.execute(
            record,
            {"mode": "result_then_child_hang", "sleep_seconds": 10},
        )
        self.assertEqual(summary["timed_out"], 1)
        attempt = self.fx.state(record)["attempts"][0]
        self.assertTrue(self.fx.path(attempt["temporary_result_path"]).is_file())
        self.assertFalse(self.fx.path(record["result_relpath"]).exists())
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["timed_out_staging_evidence"], 1)

    def test_prepared_publication_recovers_without_subprocess(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        context = self.fx.context()
        real_write = execution._write_state
        injected = {"done": False}

        def fail_result_published(ctx, state):
            attempts = state.get("attempts", [])
            status = (
                attempts[-1]["publication"]["publication_status"]
                if attempts
                else "none"
            )
            if status == "result_published" and not injected["done"]:
                injected["done"] = True
                raise OSError("injected result_published state failure")
            return real_write(ctx, state)

        with mock.patch.object(
            execution, "_write_state", side_effect=fail_result_published
        ):
            first = execution.execute_records([record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["publication"][
                "publication_status"
            ],
            "prepared",
        )
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            second = execution.execute_records([record], context, resume=True)
        self.assertEqual(second["succeeded"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 1)
        popen.assert_not_called()

    def test_orphan_staging_trace_is_not_adopted(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        context = self.fx.context()
        real_write = execution._write_state
        injected = {"done": False}

        def fail_prepared(ctx, state):
            attempts = state.get("attempts", [])
            status = (
                attempts[-1]["publication"]["publication_status"]
                if attempts
                else "none"
            )
            if status == "prepared" and not injected["done"]:
                injected["done"] = True
                raise OSError("injected prepared state failure")
            return real_write(ctx, state)

        with mock.patch.object(execution, "_write_state", side_effect=fail_prepared):
            first = execution.execute_records([record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        orphan_path = self.fx.state(record)["attempts"][0][
            "temporary_result_path"
        ]
        self.assertTrue(self.fx.path(orphan_path).is_file())
        real_popen = execution.subprocess.Popen
        with mock.patch.object(
            execution.subprocess, "Popen", wraps=real_popen
        ) as popen:
            second = execution.execute_records([record], context, resume=True)
        self.assertEqual(second["succeeded"], 1)
        self.assertEqual(popen.call_count, 1)
        state = self.fx.state(record)
        self.assertEqual(state["attempt_count"], 2)
        self.assertNotEqual(
            state["attempts"][0]["staging_directory_relpath"],
            state["attempts"][1]["staging_directory_relpath"],
        )
        self.assertTrue(self.fx.path(orphan_path).is_file())

    def test_inspector_reports_orphan_staging_trace(self):
        record = self.fx.direct_record(initial_timeout=0.15, max_attempts=1)
        self.assertEqual(
            self.execute(
                record,
                {"mode": "result_then_child_hang", "sleep_seconds": 10},
            )["timed_out"],
            1,
        )
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(
            report["staging_traces_without_prepared_metadata"], 1
        )

    def test_staging_parent_symlink_swap_cannot_escape(self):
        record = self.record_with_suffix(".txt")
        self.fx.write_inputs({"mode": "rtsim-trace-contract"}, record=record)
        outside = self.fx.base / "outside-staging-race"
        outside.mkdir()
        moved = {"path": None}

        def swap_staging(_record, context):
            staging = context["active_attempt_staging"]
            original = self.fx.path(staging["directory_relpath"])
            trusted = original.with_name(original.name + "-opened-inode")
            original.rename(trusted)
            original.symlink_to(outside, target_is_directory=True)
            moved["path"] = trusted

        with mock.patch.object(
            execution, "_before_popen_hook", side_effect=swap_staging
        ):
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["infrastructure_errors"], 0)
        self.assertEqual(
            self.fx.state(record)["attempts"][0]["termination_reason"],
            "trace_integrity_error",
        )
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((moved["path"] / "trace.txt").is_file())
        self.assertFalse(self.fx.path(record["result_relpath"]).exists())

if __name__ == "__main__":
    unittest.main()
