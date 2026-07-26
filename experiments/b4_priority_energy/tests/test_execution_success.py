import contextlib
import copy
import errno
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execute_manifest
import execution_common as execution
import manifest_common as manifest


PILOT_BYTES = manifest.render_manifest("pilot")
PILOT_RECORD = manifest.build_case(
    "pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK"
)
FAKE_SOURCE = B4_DIR / "tests" / "fake_simulator.py"


class ExecutionFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="b4pe-i4b-")
        self.base = Path(self.temporary.name)
        self.manifest_path = self.base / "pilot.jsonl"
        self.manifest_path.write_bytes(PILOT_BYTES)
        self.output_root = self.base / "output"
        self.output_root.mkdir()
        self.simulator = self.base / "fake_simulator.py"
        shutil.copyfile(FAKE_SOURCE, self.simulator)
        self.simulator.chmod(0o755)
        self.record = copy.deepcopy(PILOT_RECORD)

    def cleanup(self):
        self.temporary.cleanup()

    def path(self, relative):
        return self.output_root.joinpath(*Path(relative).parts)

    def write_inputs(self, config=None, record=None):
        record = self.record if record is None else record
        config = {"mode": "success", "result_text": "fake result\n"} if config is None else config
        values = {
            record["taskset_artifact_relpath"]: b"tasks: []\n",
            record["source_artifact_relpath"]: b'{"source":"fake"}\n',
            record["system_config_artifact_relpath"]: (
                json.dumps(config, sort_keys=True) + "\n"
            ).encode("utf-8"),
        }
        for relative, data in values.items():
            path = self.path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return values

    def context(self):
        return execution.build_context(
            self.manifest_path, self.output_root, self.simulator
        )

    def direct_record(self, initial_timeout=0.1, retry_timeout=0.2, max_attempts=2):
        record = copy.deepcopy(self.record)
        record["timeout_seconds"] = initial_timeout
        record["retry_policy"] = copy.deepcopy(record["retry_policy"])
        record["retry_policy"]["initial_timeout_seconds"] = initial_timeout
        record["retry_policy"]["retry_timeout_seconds"] = retry_timeout
        record["retry_policy"]["max_attempts"] = max_attempts
        return record

    def run_cli(self, extra=None, output_root=None):
        arguments = [
            "--manifest",
            str(self.manifest_path),
            "--output-root",
            str(self.output_root if output_root is None else output_root),
            "--simulator-binary",
            str(self.simulator),
            "--execute",
            "--limit",
            "1",
        ]
        if extra:
            arguments.extend(extra)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = execute_manifest.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def state(self, record=None):
        record = self.record if record is None else record
        path = self.path(f".b4pe/state/{record['case_id']}.json")
        return json.loads(path.read_text(encoding="utf-8"))


class ExecutionSuccessTests(unittest.TestCase):
    def setUp(self):
        self.fx = ExecutionFixture()
        self.fx.write_inputs({"mode": "success", "stdout": "hello", "stderr": "warning", "result_text": "done\n"})

    def tearDown(self):
        self.fx.cleanup()

    def test_successful_execution(self):
        status, output, errors = self.fx.run_cli()
        self.assertEqual(status, 0, errors)
        summary = json.loads(output)
        self.assertEqual(summary["executed_cases"], 1)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(self.fx.path(self.fx.record["result_relpath"]).read_text(), "done\n")

    def test_result_is_atomically_published(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        temporary_files = list(self.fx.path(".b4pe/tmp").iterdir())
        self.assertEqual(temporary_files, [])
        self.assertTrue(self.fx.path(self.fx.record["result_relpath"]).is_file())

    def test_stdout_and_stderr_are_published(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        case_id = self.fx.record["case_id"]
        self.assertEqual(self.fx.path(f".b4pe/logs/{case_id}.stdout").read_text(), "hello\n")
        self.assertEqual(self.fx.path(f".b4pe/logs/{case_id}.stderr").read_text(), "warning\n")
        self.assertTrue(self.fx.path(f".b4pe/logs/{case_id}.attempt-1.stdout").is_file())
        self.assertTrue(self.fx.path(f".b4pe/logs/{case_id}.attempt-1.stderr").is_file())

    def test_state_contains_required_provenance(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        state = self.fx.state()
        required = {
            "schema_version", "case_id", "phase", "algorithm",
            "manifest_file_sha256", "manifest_record_sha256",
            "execution_protocol_sha256", "simulator_binary_path",
            "simulator_binary_sha256", "taskset_artifact_sha256",
            "source_artifact_sha256", "attempt_count", "current_status",
            "attempts", "final_result_sha256", "stdout_sha256", "stderr_sha256",
        }
        self.assertTrue(required <= set(state))
        self.assertEqual(state["current_status"], "succeeded")
        self.assertEqual(state["attempt_count"], 1)

    def test_attempt_contains_required_metadata(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        attempt = self.fx.state()["attempts"][0]
        required = {
            "attempt_index", "timeout_seconds", "started_at", "ended_at",
            "exit_code", "termination_reason", "stdout_sha256", "stderr_sha256",
            "temporary_result_path", "final_result_sha256",
            "staging_directory_relpath", "staging_trace_basename",
            "staging_trace_sha256",
            "publication",
            "snapshot_execution",
        }
        self.assertEqual(set(attempt), required)
        self.assertEqual(attempt["termination_reason"], "succeeded")
        self.assertEqual(attempt["staging_trace_basename"], "trace.txt")
        self.assertRegex(
            attempt["staging_directory_relpath"],
            rf"^\.b4pe/attempt-results/{self.fx.record['case_id']}/attempt-0001-[0-9a-f]{{24}}$",
        )

    def test_taskset_and_source_sha_are_correct(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        state = self.fx.state()
        for role in ("taskset", "source"):
            path = self.fx.path(self.fx.record[f"{role}_artifact_relpath"])
            self.assertEqual(state[f"{role}_artifact_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_simulator_binary_sha_is_correct(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        self.assertEqual(
            self.fx.state()["simulator_binary_sha256"],
            hashlib.sha256(self.fx.simulator.read_bytes()).hexdigest(),
        )

    def test_popen_uses_shell_false_and_new_session(self):
        real_popen = execution.subprocess.Popen
        calls = []

        def recording_popen(*args, **kwargs):
            calls.append(kwargs.copy())
            return real_popen(*args, **kwargs)

        with mock.patch.object(execution.subprocess, "Popen", side_effect=recording_popen):
            self.assertEqual(self.fx.run_cli()[0], 0)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["shell"], False)
        self.assertIs(calls[0]["start_new_session"], True)

    def test_manifest_without_semantic_hash_is_not_augmented(self):
        self.assertNotIn(
            "--taskset-semantic-hash", self.fx.record["command_argv"]
        )
        real_popen = execution.subprocess.Popen
        observed = []

        def recording_popen(argv, **kwargs):
            observed.append(list(argv))
            return real_popen(argv, **kwargs)

        with mock.patch.object(
            execution.subprocess, "Popen", side_effect=recording_popen
        ):
            self.assertEqual(
                execution.execute_records(
                    [self.fx.record], self.fx.context()
                )["succeeded"],
                1,
            )
        self.assertEqual(len(observed), 1)
        self.assertNotIn("--taskset-semantic-hash", observed[0])

    def test_manifest_semantic_arguments_are_preserved_without_append(self):
        record = copy.deepcopy(self.fx.record)
        semantic_hash = "a" * 64
        record["command_argv"].extend(
            ["--taskset-semantic-hash", semantic_hash, "--semantic-traces"]
        )
        self.fx.write_inputs(
            {"mode": "success", "result_text": "semantic-plan\n"},
            record=record,
        )
        real_popen = execution.subprocess.Popen
        observed = []

        def recording_popen(argv, **kwargs):
            observed.append(list(argv))
            return real_popen(argv, **kwargs)

        with mock.patch.object(
            execution.subprocess, "Popen", side_effect=recording_popen
        ):
            self.assertEqual(
                execution.execute_records([record], self.fx.context())[
                    "succeeded"
                ],
                1,
            )
        self.assertEqual(len(observed), 1)
        actual = observed[0]
        self.assertEqual(len(actual), len(record["command_argv"]))
        self.assertEqual(
            actual[-3:],
            ["--taskset-semantic-hash", semantic_hash, "--semantic-traces"],
        )
        replacements = {
            record["system_config_artifact_relpath"],
            record["taskset_artifact_relpath"],
            record["result_relpath"],
            record["command_argv"][0],
        }
        for planned, executed in zip(record["command_argv"], actual):
            if planned not in replacements:
                self.assertEqual(executed, planned)

    def test_production_argv_builder_has_no_test_override_hook(self):
        source = (B4_DIR / "execution_common.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_execution_argv_hook", source)
        self.assertNotIn('"--taskset-semantic-hash"', source)

    def test_success_closes_snapshot_file_descriptors_without_leak(self):
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
                summary = execution.execute_records([self.fx.record], context)
            after = len(os.listdir(execution.PROC_FD_ROOT))
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(after, before)
            self.assertEqual(len(captured), 4)
            for descriptor in captured:
                with self.assertRaises(OSError) as raised:
                    os.fstat(descriptor)
                self.assertEqual(raised.exception.errno, errno.EBADF)
        finally:
            execution.close_context(context)

    def test_multiple_cases_keep_parent_fd_count_stable(self):
        records = manifest.validate_manifest(self.fx.manifest_path)[:4]
        for record in records:
            self.fx.write_inputs(
                {"mode": "success", "result_text": f"{record['case_id']}\n"},
                record=record,
            )
        context = self.fx.context()
        try:
            before = len(os.listdir(execution.PROC_FD_ROOT))
            summary = execution.execute_records(records, context)
            after = len(os.listdir(execution.PROC_FD_ROOT))
            self.assertEqual(summary["succeeded"], len(records))
            self.assertEqual(after, before)
        finally:
            execution.close_context(context)

    def test_summary_is_stable_across_output_roots(self):
        first = self.fx.run_cli()[1]
        second_root = self.fx.base / "second-output"
        second_root.mkdir()
        original = self.fx.output_root
        self.fx.output_root = second_root
        self.fx.write_inputs({"mode": "success", "stdout": "hello", "stderr": "warning", "result_text": "done\n"})
        second = self.fx.run_cli()[1]
        self.fx.output_root = original
        self.assertEqual(first, second)

    def test_limit_one_creates_one_state(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        states = list(self.fx.path(".b4pe/state").glob("*.json"))
        self.assertEqual(len(states), 1)

    def test_result_without_state_fails_closed(self):
        result = self.fx.path(self.fx.record["result_relpath"])
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("foreign\n", encoding="utf-8")
        status, output, _ = self.fx.run_cli()
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["infrastructure_errors"], 1)
        self.assertEqual(result.read_text(), "foreign\n")

    def test_state_records_read_only_content_addressed_snapshots(self):
        self.assertEqual(self.fx.run_cli()[0], 0)
        state = self.fx.state()
        for role in ("simulator", "system", "taskset", "source"):
            relative = state[f"{role}_snapshot_relpath"]
            snapshot = self.fx.path(relative)
            self.assertEqual(snapshot.name, state[f"{role}_snapshot_sha256"])
            self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.name)
            self.assertEqual(snapshot.stat().st_mode & 0o222, 0)
            self.assertEqual(
                state[f"{role}_execution_transport"],
                "inherited_file_descriptor",
            )
            self.assertEqual(
                state[f"{role}_executed_snapshot_sha256"],
                state[f"{role}_snapshot_sha256"],
            )
            proc_path = state[f"{role}_executed_proc_fd_path"]
            self.assertRegex(proc_path, r"^/proc/self/fd/[0-9]+$")
        self.assertNotEqual(state["simulator_executed_snapshot_path"], str(self.fx.simulator))

    def test_summary_filename_matches_canonical_content_sha(self):
        output = self.fx.run_cli()[1]
        summaries = list(self.fx.path(".b4pe/summaries").glob("*.json"))
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].read_text(encoding="utf-8"), output)
        self.assertEqual(hashlib.sha256(summaries[0].read_bytes()).hexdigest(), summaries[0].stem)

    def test_same_summary_concurrent_publication_is_idempotent(self):
        context_one = self.fx.context()
        context_two = self.fx.context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            summaries = list(
                pool.map(lambda context: execution.execute_records([], context), (context_one, context_two))
            )
        self.assertEqual(summaries[0], summaries[1])
        summaries = list(self.fx.path(".b4pe/summaries").glob("*.json"))
        self.assertEqual(len(summaries), 1)
        json.loads(summaries[0].read_text(encoding="utf-8"))

    def test_different_concurrent_summaries_do_not_overwrite(self):
        context_one = self.fx.context()
        context_two = self.fx.context()
        records = manifest.validate_manifest(self.fx.manifest_path)[:3]
        for record in records:
            self.fx.write_inputs(
                {"mode": "success", "result_text": f"{record['case_id']}\n"},
                record=record,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            summaries = list(
                pool.map(
                    lambda pair: execution.execute_records(pair[0], pair[1]),
                    ((records[:1], context_one), (records[1:], context_two)),
                )
            )
        self.assertEqual([value["selected_cases"] for value in summaries], [1, 2])
        self.assertEqual([value["succeeded"] for value in summaries], [1, 2])
        values = {
            json.loads(path.read_text(encoding="utf-8"))["selected_cases"]
            for path in self.fx.path(".b4pe/summaries").glob("*.json")
        }
        self.assertEqual(values, {1, 2})


if __name__ == "__main__":
    unittest.main()
