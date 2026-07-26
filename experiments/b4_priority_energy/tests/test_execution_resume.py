import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
from test_execution_success import ExecutionFixture


def attempt_record(case_id, index, reason=None, running=False):
    staging_directory = (
        f".b4pe/attempt-results/{case_id}/attempt-{index:04d}-persisted"
    )
    attempt = {
        "attempt_index": index,
        "timeout_seconds": 0.1,
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": None if running else "2026-01-01T00:00:01+00:00",
        "exit_code": None if running or reason == "timeout" else 1,
        "termination_reason": None if running else reason,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "temporary_result_path": f"{staging_directory}/trace.txt",
        "staging_directory_relpath": staging_directory,
        "staging_trace_basename": "trace.txt",
        "staging_trace_sha256": None,
        "final_result_sha256": None,
        "publication": execution._new_publication(),
        "snapshot_execution": {},
    }
    attempt["publication"]["staging_result_relpath"] = attempt[
        "temporary_result_path"
    ]
    return attempt


class ExecutionResumeTests(unittest.TestCase):
    def setUp(self):
        self.fx = ExecutionFixture()
        self.fx.write_inputs({"mode": "success", "result_text": "done\n"})

    def tearDown(self):
        self.fx.cleanup()

    def persist(self, record, status, reasons):
        context = self.fx.context()
        provenance = execution.build_provenance(record, context)
        state = execution.new_state(provenance)
        state["attempts"] = [
            attempt_record(
                record["case_id"],
                index,
                reason,
                running=status == "running" and index == len(reasons),
            )
            for index, reason in enumerate(reasons, 1)
        ]
        state["attempt_count"] = len(state["attempts"])
        state["current_status"] = status
        execution._write_state(context, state)
        return context, state

    def fail_state_update(self, publication_status):
        real_write = execution._write_state
        injected = {"done": False}

        def faulty_write(context, state):
            attempts = state.get("attempts", [])
            status = (
                attempts[-1]["publication"]["publication_status"]
                if attempts
                else "none"
            )
            if status == publication_status and not injected["done"]:
                injected["done"] = True
                raise OSError(f"injected {publication_status} state failure")
            return real_write(context, state)

        return faulty_write

    def test_succeeded_resume_is_skipped(self):
        context = self.fx.context()
        first = execution.execute_records([self.fx.record], context)
        second = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(first["succeeded"], 1)
        self.assertEqual(second["skipped_succeeded"], 1)
        self.assertEqual(self.fx.state()["attempt_count"], 1)

    def test_tampered_succeeded_result_fails_closed(self):
        context = self.fx.context()
        self.assertEqual(execution.execute_records([self.fx.record], context)["succeeded"], 1)
        self.fx.path(self.fx.record["result_relpath"]).write_text("tampered\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_succeeded_state_fingerprint_drift_fails_closed(self):
        context = self.fx.context()
        self.assertEqual(execution.execute_records([self.fx.record], context)["succeeded"], 1)
        state_path = self.fx.path(f".b4pe/state/{self.fx.record['case_id']}.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["manifest_record_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_running_without_resume_marks_interrupted(self):
        record = self.fx.direct_record()
        context, _ = self.persist(record, "running", [None])
        summary = execution.execute_records([record], context)
        self.assertEqual(summary["interrupted"], 1)
        self.assertEqual(self.fx.state(record)["current_status"], "interrupted")

    def test_running_with_resume_continues_without_reset(self):
        record = self.fx.direct_record(retry_timeout=1)
        context, _ = self.persist(record, "running", [None])
        summary = execution.execute_records([record], context, resume=True)
        state = self.fx.state(record)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(state["attempt_count"], 2)
        self.assertEqual(state["attempts"][0]["termination_reason"], "interrupted")

    def test_failed_without_retry_failed_is_skipped(self):
        self.fx.write_inputs({"mode": "nonzero"})
        record = self.fx.direct_record()
        context = self.fx.context()
        self.assertEqual(execution.execute_records([record], context)["failed"], 1)
        second = execution.execute_records([record], context)
        self.assertEqual(second["skipped_failed"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 1)

    def test_timed_out_with_retry_failed_continues(self):
        self.fx.write_inputs({"mode": "first_timeout_then_success", "result_text": "resumed\n"})
        record = self.fx.direct_record(retry_timeout=1)
        context, _ = self.persist(record, "timed_out", ["timeout"])
        summary = execution.execute_records([record], context, retry_failed=True)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 2)

    def test_nonretryable_failed_state_is_rejected(self):
        self.fx.write_inputs({"mode": "nonzero"})
        record = self.fx.direct_record()
        context, _ = self.persist(record, "failed", ["nonzero_exit"])
        summary = execution.execute_records([record], context, retry_failed=True)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 1)

    def test_exhausted_attempts_are_rejected(self):
        record = self.fx.direct_record(max_attempts=2)
        context, _ = self.persist(record, "timed_out", ["timeout", "timeout"])
        summary = execution.execute_records([record], context, retry_failed=True)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 2)

    def test_lock_conflict_prevents_duplicate_execution(self):
        context = self.fx.context()
        with execution.case_lock(context["output_root"], self.fx.record["case_id"]):
            with mock.patch.object(execution.subprocess, "Popen") as popen:
                summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["lock_conflicts"], 1)
        popen.assert_not_called()

    def test_prepared_state_is_durable_before_result_publication(self):
        context = self.fx.context()
        observed = []
        real_replace = execution._replace_at

        def inspect_before_replace(
            ctx,
            temporary,
            final,
            allow_existing=False,
            expected_sha256=None,
            post_replace_verifier=None,
        ):
            if final == self.fx.record["result_relpath"]:
                observed.append(self.fx.state()["attempts"][-1]["publication"]["publication_status"])
                raise OSError("stop after prepared")
            return real_replace(
                ctx,
                temporary,
                final,
                allow_existing,
                expected_sha256,
                post_replace_verifier,
            )

        with mock.patch.object(execution, "_replace_at", side_effect=inspect_before_replace):
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(observed, ["prepared"])
        self.assertFalse(self.fx.path(self.fx.record["result_relpath"]).exists())

    def test_prepared_temporary_recovers_with_zero_execution_setup(self):
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
            if final == self.fx.record["result_relpath"]:
                raise OSError("crash before result replace")
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
            first = execution.execute_records([self.fx.record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        before = self.fx.state()
        publication = before["attempts"][0]["publication"]
        self.assertEqual(publication["publication_status"], "prepared")
        self.assertTrue(
            self.fx.path(publication["temporary_result_relpath"]).is_file()
        )
        self.assertFalse(
            self.fx.path(publication["final_result_relpath"]).exists()
        )
        with mock.patch.object(
            execution.subprocess, "Popen"
        ) as popen, mock.patch.object(
            execution, "_open_execution_snapshots"
        ) as open_snapshots, mock.patch.object(
            execution, "_create_attempt_staging"
        ) as create_staging:
            second = execution.execute_records(
                [self.fx.record], context, resume=True
            )
        self.assertEqual(second["succeeded"], 1)
        popen.assert_not_called()
        open_snapshots.assert_not_called()
        create_staging.assert_not_called()
        state = self.fx.state()
        self.assertEqual(state["attempt_count"], 1)
        publication = state["attempts"][0]["publication"]
        self.assertEqual(publication["publication_status"], "committed")
        self.assertEqual(
            publication["observed_final_result_sha256"],
            publication["expected_result_sha256"],
        )

    def test_tampered_prepared_temporary_persists_failed_state(self):
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
            if final == self.fx.record["result_relpath"]:
                raise OSError("crash before result replace")
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
            execution.execute_records([self.fx.record], context)
        publication = self.fx.state()["attempts"][0]["publication"]
        temporary = self.fx.path(publication["temporary_result_relpath"])
        temporary.write_text("tampered temporary\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records(
                [self.fx.record], context, resume=True
            )
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["infrastructure_errors"], 0)
        popen.assert_not_called()
        state = self.fx.state()
        attempt = state["attempts"][0]
        self.assertEqual(state["current_status"], "failed")
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(attempt["termination_reason"], "trace_integrity_error")
        self.assertEqual(
            attempt["publication"]["publication_status"], "prepared"
        )
        self.assertTrue(temporary.is_file())
        self.assertFalse(
            self.fx.path(self.fx.record["result_relpath"]).exists()
        )

    def test_result_published_state_failure_resumes_without_subprocess(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("result_published")
        ):
            first = execution.execute_records([self.fx.record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        self.assertEqual(self.fx.state()["attempts"][0]["publication"]["publication_status"], "prepared")
        with mock.patch.object(
            execution, "_open_execution_snapshots"
        ) as open_snapshots, mock.patch.object(
            execution.subprocess, "Popen"
        ) as popen:
            resumed = execution.execute_records(
                [self.fx.record], context, resume=True
            )
        self.assertEqual(resumed["succeeded"], 1)
        open_snapshots.assert_not_called()
        popen.assert_not_called()
        state = self.fx.state()
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["attempts"][0]["publication"]["publication_status"], "committed")
        for relative, field in (
            (self.fx.record["result_relpath"], "final_result_sha256"),
            (f".b4pe/logs/{self.fx.record['case_id']}.stdout", "stdout_sha256"),
            (f".b4pe/logs/{self.fx.record['case_id']}.stderr", "stderr_sha256"),
        ):
            self.assertEqual(
                hashlib.sha256(self.fx.path(relative).read_bytes()).hexdigest(),
                state[field],
            )

    def test_logs_published_state_failure_resumes_without_subprocess(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("logs_published")
        ):
            first = execution.execute_records([self.fx.record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        self.assertEqual(self.fx.state()["attempts"][0]["publication"]["publication_status"], "result_published")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            resumed = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(resumed["succeeded"], 1)
        popen.assert_not_called()
        self.assertEqual(self.fx.state()["attempt_count"], 1)

    def test_final_succeeded_state_failure_resumes_same_attempt(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("committed")
        ):
            first = execution.execute_records([self.fx.record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        self.assertEqual(
            self.fx.state()["attempts"][0]["publication"]["publication_status"],
            "logs_published",
        )
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            resumed = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(resumed["succeeded"], 1)
        self.assertEqual(self.fx.state()["attempt_count"], 1)
        popen.assert_not_called()

    def test_logs_published_tampered_result_persists_failed_state(self):
        context = self.fx.context()
        with mock.patch.object(
            execution,
            "_write_state",
            side_effect=self.fail_state_update("committed"),
        ):
            first = execution.execute_records([self.fx.record], context)
        self.assertEqual(first["infrastructure_errors"], 1)
        before = self.fx.state()
        self.assertEqual(
            before["attempts"][0]["publication"]["publication_status"],
            "logs_published",
        )
        result = self.fx.path(self.fx.record["result_relpath"])
        result.write_text("tampered after logs\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            second = execution.execute_records(
                [self.fx.record], context, resume=True
            )
        self.assertEqual(second["failed"], 1)
        self.assertEqual(second["infrastructure_errors"], 0)
        popen.assert_not_called()
        state = self.fx.state()
        attempt = state["attempts"][0]
        self.assertEqual(state["current_status"], "failed")
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(attempt["termination_reason"], "trace_integrity_error")
        self.assertEqual(
            attempt["publication"]["publication_status"],
            "logs_published",
        )
        self.assertNotEqual(
            attempt["publication"]["observed_final_result_sha256"],
            attempt["publication"]["expected_result_sha256"],
        )

    def test_publication_resume_is_independent_of_root_fd_number(self):
        first_context = self.fx.context()
        first_fd = first_context["root_fd"]
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("result_published")
        ):
            execution.execute_records([self.fx.record], first_context)
        execution.close_context(first_context)
        held = [os.open("/dev/null", os.O_RDONLY) for _ in range(3)]
        try:
            second_context = self.fx.context()
            self.assertNotEqual(first_fd, second_context["root_fd"])
            with mock.patch.object(execution.subprocess, "Popen") as popen:
                resumed = execution.execute_records(
                    [self.fx.record], second_context, resume=True
                )
            self.assertEqual(resumed["succeeded"], 1)
            self.assertEqual(self.fx.state()["attempt_count"], 1)
            popen.assert_not_called()
        finally:
            for descriptor in held:
                os.close(descriptor)

    def test_publication_recovery_uses_frozen_snapshots_not_changed_originals(self):
        first_context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("result_published")
        ):
            execution.execute_records([self.fx.record], first_context)
        for field in (
            "taskset_artifact_relpath",
            "source_artifact_relpath",
            "system_config_artifact_relpath",
        ):
            self.fx.path(self.fx.record[field]).write_text("changed\n", encoding="utf-8")
        self.fx.simulator.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            resumed = execution.execute_records(
                [self.fx.record], first_context, resume=True
            )
        self.assertEqual(resumed["succeeded"], 1)
        self.assertEqual(self.fx.state()["attempt_count"], 1)
        popen.assert_not_called()

    def test_prepared_state_failure_publishes_no_final_files(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("prepared")
        ):
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertFalse(self.fx.path(self.fx.record["result_relpath"]).exists())
        case_id = self.fx.record["case_id"]
        self.assertFalse(self.fx.path(f".b4pe/logs/{case_id}.stdout").exists())
        self.assertFalse(self.fx.path(f".b4pe/logs/{case_id}.stderr").exists())
        self.assertNotEqual(self.fx.state()["current_status"], "succeeded")

    def test_orphan_result_with_running_state_fails_without_subprocess(self):
        record = self.fx.direct_record()
        context, _ = self.persist(record, "running", [None])
        result = self.fx.path(record["result_relpath"])
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("orphan\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([record], context, resume=True)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(result.read_text(encoding="utf-8"), "orphan\n")
        self.assertEqual(self.fx.state(record)["attempt_count"], 1)
        popen.assert_not_called()

    def test_tampered_prepared_result_fails_without_subprocess(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("result_published")
        ):
            execution.execute_records([self.fx.record], context)
        result = self.fx.path(self.fx.record["result_relpath"])
        result.write_text("tampered\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], context, resume=True)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["infrastructure_errors"], 0)
        self.assertEqual(result.read_text(encoding="utf-8"), "tampered\n")
        state = self.fx.state()
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["current_status"], "failed")
        attempt = state["attempts"][0]
        self.assertEqual(attempt["termination_reason"], "trace_integrity_error")
        self.assertEqual(
            attempt["publication"]["publication_status"], "prepared"
        )
        self.assertNotEqual(
            attempt["publication"]["observed_final_result_sha256"],
            attempt["publication"]["expected_result_sha256"],
        )
        popen.assert_not_called()

    def test_partial_publication_without_resume_does_not_mutate_state(self):
        context = self.fx.context()
        with mock.patch.object(
            execution, "_write_state", side_effect=self.fail_state_update("result_published")
        ):
            execution.execute_records([self.fx.record], context)
        before = self.fx.path(f".b4pe/state/{self.fx.record['case_id']}.json").read_bytes()
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["interrupted"], 1)
        self.assertEqual(
            self.fx.path(f".b4pe/state/{self.fx.record['case_id']}.json").read_bytes(),
            before,
        )
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
