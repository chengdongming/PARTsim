import json
import errno
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
from test_execution_success import ExecutionFixture


class ExecutionRetryTests(unittest.TestCase):
    def setUp(self):
        self.fx = ExecutionFixture()

    def tearDown(self):
        self.fx.cleanup()

    def execute(self, record):
        context = self.fx.context()
        try:
            return execution.execute_records([record], context)
        finally:
            execution.close_context(context)

    def test_nonzero_exit_is_failed_without_retry(self):
        self.fx.write_inputs({"mode": "nonzero", "exit_code": 7})
        record = self.fx.direct_record()
        summary = self.execute(record)
        state = self.fx.state(record)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["attempts"][0]["termination_reason"], "nonzero_exit")

    def test_timeout_is_classified(self):
        self.fx.write_inputs({"mode": "sleep", "sleep_seconds": 10})
        record = self.fx.direct_record(initial_timeout=0.05, max_attempts=1)
        summary = self.execute(record)
        self.assertEqual(summary["timed_out"], 1)
        self.assertEqual(self.fx.state(record)["attempts"][0]["termination_reason"], "timeout")

    def test_timeout_closes_snapshot_file_descriptors_without_leak(self):
        self.fx.write_inputs({"mode": "sleep", "sleep_seconds": 10})
        record = self.fx.direct_record(initial_timeout=0.05, max_attempts=1)
        context = self.fx.context()
        captured = []

        def record_fds(_record, current):
            captured.extend(
                item["fd"]
                for item in current["active_snapshot_execution"].values()
            )
            for descriptor in captured:
                os.fstat(descriptor)

        try:
            before = len(os.listdir(execution.PROC_FD_ROOT))
            with mock.patch.object(
                execution, "_before_popen_hook", side_effect=record_fds
            ):
                summary = execution.execute_records([record], context)
            after = len(os.listdir(execution.PROC_FD_ROOT))
            self.assertEqual(summary["timed_out"], 1)
            self.assertEqual(after, before)
            self.assertEqual(len(captured), 4)
            for descriptor in captured:
                with self.assertRaises(OSError) as raised:
                    os.fstat(descriptor)
                self.assertEqual(raised.exception.errno, errno.EBADF)
        finally:
            execution.close_context(context)

    def test_timeout_retries_then_succeeds(self):
        self.fx.write_inputs(
            {"mode": "first_timeout_then_success", "sleep_seconds": 10, "result_text": "retry success\n"}
        )
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=1)
        summary = self.execute(record)
        state = self.fx.state(record)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(state["attempt_count"], 2)
        self.assertEqual(
            [attempt["termination_reason"] for attempt in state["attempts"]],
            ["timeout", "succeeded"],
        )

    def test_first_nonzero_then_success_is_not_retried(self):
        self.fx.write_inputs({"mode": "first_fail_then_success"})
        record = self.fx.direct_record()
        summary = self.execute(record)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 1)

    def test_no_third_attempt(self):
        self.fx.write_inputs({"mode": "sleep", "sleep_seconds": 10})
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=0.05)
        summary = self.execute(record)
        self.assertEqual(summary["timed_out"], 1)
        self.assertEqual(self.fx.state(record)["attempt_count"], 2)

    def test_success_without_result_is_failed(self):
        self.fx.write_inputs({"mode": "missing_result"})
        record = self.fx.direct_record()
        summary = self.execute(record)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(self.fx.state(record)["attempts"][0]["termination_reason"], "missing_result")

    def test_empty_result_is_failed(self):
        self.fx.write_inputs({"mode": "empty_result"})
        record = self.fx.direct_record()
        summary = self.execute(record)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(self.fx.state(record)["attempts"][0]["termination_reason"], "empty_result")

    def test_timeout_kills_spawned_process_group(self):
        pid_path = self.fx.base / "child.pid"
        self.fx.write_inputs(
            {
                "mode": "result_then_child_hang",
                "sleep_seconds": 10,
                "child_pid_path": str(pid_path),
                "child_ignore_sigterm": True,
            }
        )
        record = self.fx.direct_record(initial_timeout=0.15, max_attempts=1)
        summary = self.execute(record)
        self.assertEqual(summary["timed_out"], 1)
        child_pid = int(pid_path.read_text(encoding="ascii"))
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("fake simulator child survived process-group termination")

    def test_retry_attempt_logs_are_independent(self):
        self.fx.write_inputs(
            {"mode": "first_timeout_then_success", "sleep_seconds": 10, "stdout": "attempt"}
        )
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=1)
        self.assertEqual(self.execute(record)["succeeded"], 1)
        case_id = record["case_id"]
        for index in (1, 2):
            self.assertTrue(
                self.fx.path(f".b4pe/logs/{case_id}.attempt-{index}.stdout").is_file()
            )

    def test_attempts_use_contract_timeouts(self):
        self.fx.write_inputs(
            {"mode": "first_timeout_then_success", "sleep_seconds": 10}
        )
        record = self.fx.direct_record(initial_timeout=0.05, retry_timeout=0.75)
        self.assertEqual(self.execute(record)["succeeded"], 1)
        self.assertEqual(
            [attempt["timeout_seconds"] for attempt in self.fx.state(record)["attempts"]],
            [0.05, 0.75],
        )


if __name__ == "__main__":
    unittest.main()
