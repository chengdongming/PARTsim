import copy
import errno
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import inspect_execution
from test_execution_success import ExecutionFixture


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fx = ExecutionFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_relative_path_escape_is_rejected(self):
        root = execution.validate_output_root(self.fx.output_root)
        with self.assertRaisesRegex(Exception, "parent traversal"):
            execution.safe_output_path(root, "../escape")

    def test_symlink_escape_is_rejected(self):
        outside = self.fx.base / "outside"
        outside.mkdir()
        (self.fx.output_root / "artifacts").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(execution.SafetyError, "symlink|escapes output-root"):
            execution.build_provenance(self.fx.record, self.fx.context())

    def test_missing_taskset_fails_closed(self):
        self.fx.write_inputs()
        self.fx.path(self.fx.record["taskset_artifact_relpath"]).unlink()
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_missing_source_fails_closed(self):
        self.fx.write_inputs()
        self.fx.path(self.fx.record["source_artifact_relpath"]).unlink()
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_missing_system_config_fails_closed(self):
        self.fx.write_inputs()
        self.fx.path(self.fx.record["system_config_artifact_relpath"]).unlink()
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_result_relpath_missing_from_argv_fails(self):
        self.fx.write_inputs()
        record = copy.deepcopy(self.fx.record)
        record["command_argv"] = [
            item for item in record["command_argv"] if item != record["result_relpath"]
        ]
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_result_relpath_duplicate_in_argv_fails(self):
        self.fx.write_inputs()
        record = copy.deepcopy(self.fx.record)
        record["command_argv"].append(record["result_relpath"])
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_inspector_reports_success(self):
        self.fx.write_inputs()
        self.assertEqual(
            execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1
        )
        report = inspect_execution.inspect_output(
            self.fx.output_root, self.fx.manifest_path, self.fx.simulator
        )
        self.assertEqual(report["state_count"], 1)
        self.assertEqual(report["status_counts"]["succeeded"], 1)
        self.assertEqual(report["missing_results"], 0)
        self.assertEqual(report["sha_mismatches"], 0)

    def test_inspector_finds_tampered_result(self):
        self.fx.write_inputs()
        self.assertEqual(
            execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1
        )
        self.fx.path(self.fx.record["result_relpath"]).write_text("tampered\n", encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["sha_mismatches"], 1)

    def test_inspector_finds_input_fingerprint_drift(self):
        self.fx.write_inputs()
        self.assertEqual(
            execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1
        )
        self.fx.path(self.fx.record["taskset_artifact_relpath"]).write_text("changed\n", encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["input_fingerprint_drift"], 1)

    def test_inspector_does_not_modify_files(self):
        self.fx.write_inputs()
        self.assertEqual(
            execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1
        )
        before = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        inspect_execution.inspect_output(
            self.fx.output_root, self.fx.manifest_path, self.fx.simulator
        )
        after = {
            path.relative_to(self.fx.output_root).as_posix(): path.read_bytes()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_inspector_reports_invalid_state(self):
        root = execution.validate_output_root(self.fx.output_root)
        execution.ensure_layout(root)
        self.fx.path(".b4pe/state/bad.json").write_text("{bad}\n", encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["state_count"], 1)
        self.assertEqual(report["invalid_states"], 1)

    def test_executor_never_uses_os_system(self):
        source = (B4_DIR / "execution_common.py").read_text(encoding="utf-8")
        self.assertNotIn("os.system(", source)
        self.assertNotIn("shell=True", source)

    def _run_with_post_snapshot_mutation(self, role):
        original_values = self.fx.write_inputs(
            {"mode": "snapshot_digest", "result_text": "unused\n"}
        )
        role_paths = {
            "system": self.fx.path(self.fx.record["system_config_artifact_relpath"]),
            "taskset": self.fx.path(self.fx.record["taskset_artifact_relpath"]),
            "source": self.fx.path(self.fx.record["source_artifact_relpath"]),
            "simulator": self.fx.simulator,
        }
        before = role_paths[role].read_bytes()

        def mutate(_record, _context):
            role_paths[role].write_bytes(b"changed after immutable snapshot\n")

        context = self.fx.context()
        with mock.patch.object(execution, "_before_popen_hook", side_effect=mutate):
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["succeeded"], 1)
        state = self.fx.state()
        self.assertEqual(
            state[f"{role}_snapshot_sha256"], hashlib.sha256(before).hexdigest()
        )
        self.assertEqual(
            state[f"{role}_observed_original_sha256"],
            state[f"{role}_snapshot_sha256"],
        )
        if role != "simulator":
            payload = json.loads(
                self.fx.path(self.fx.record["result_relpath"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload[f"{role}_sha256"], state[f"{role}_snapshot_sha256"]
            )
        return original_values

    def test_taskset_snapshot_is_used_after_original_changes(self):
        self._run_with_post_snapshot_mutation("taskset")

    def test_source_snapshot_is_used_after_original_changes(self):
        self._run_with_post_snapshot_mutation("source")

    def test_system_snapshot_is_used_after_original_changes(self):
        self._run_with_post_snapshot_mutation("system")

    def test_simulator_snapshot_executes_after_original_changes(self):
        self._run_with_post_snapshot_mutation("simulator")

    def test_subprocess_argv_and_environment_use_snapshots(self):
        self.fx.write_inputs({"mode": "snapshot_digest"})
        real_popen = execution.subprocess.Popen
        calls = []

        def recording_popen(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            for descriptor in kwargs["pass_fds"]:
                os.fstat(descriptor)
            return real_popen(argv, **kwargs)

        context = self.fx.context()
        with mock.patch.object(execution.subprocess, "Popen", side_effect=recording_popen):
            self.assertEqual(
                execution.execute_records([self.fx.record], context)["succeeded"],
                1,
            )
        argv, kwargs = calls[0]
        environment = kwargs["env"]
        input_paths = [
            argv[0],
            argv[1],
            argv[2],
            environment["B4PE_SOURCE_SNAPSHOT"],
        ]
        for path in input_paths:
            self.assertRegex(path, r"^/proc/self/fd/[0-9]+$")
        input_fds = tuple(int(path.rsplit("/", 1)[1]) for path in input_paths)
        result_path = argv[argv.index("-t") + 1]
        self.assertRegex(result_path, r"^/proc/self/fd/[0-9]+/[^/]+$")
        result_parent_fd = int(result_path.split("/")[4])
        self.assertEqual(kwargs["pass_fds"], input_fds + (result_parent_fd,))
        self.assertNotIn(context["root_fd"], kwargs["pass_fds"])
        self.assertEqual(len(kwargs["pass_fds"]), 5)
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertNotIn(str(self.fx.simulator), argv)

    def test_input_change_during_snapshot_copy_fails_closed(self):
        self.fx.write_inputs()
        taskset = self.fx.path(self.fx.record["taskset_artifact_relpath"])

        def mutate(role, stage, _path):
            if role == "taskset" and stage == "after_first_read":
                taskset.write_text("changed during copy\n", encoding="utf-8")

        with mock.patch.object(execution, "_snapshot_copy_hook", side_effect=mutate), mock.patch.object(
            execution.subprocess, "Popen"
        ) as popen:
            summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertFalse(self.fx.path(self.fx.record["result_relpath"]).exists())
        popen.assert_not_called()

    def test_existing_snapshot_sha_mismatch_fails_closed(self):
        self.fx.write_inputs()
        context = self.fx.context()
        provenance = execution.build_provenance(self.fx.record, context)
        snapshot = self.fx.path(provenance["taskset_snapshot_relpath"])
        snapshot.chmod(0o600)
        snapshot.write_text("corrupt snapshot\n", encoding="utf-8")
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        popen.assert_not_called()

    def test_output_root_symlink_is_rejected(self):
        target = self.fx.base / "target-output"
        target.mkdir()
        link = self.fx.base / "linked-output"
        link.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.fx.run_cli(output_root=link)[0], 1)

    def test_result_parent_symlink_is_rejected(self):
        self.fx.write_inputs()
        outside = self.fx.base / "outside-result"
        outside.mkdir()
        result_parent = self.fx.path(self.fx.record["result_relpath"]).parent
        result_parent.parent.mkdir(parents=True, exist_ok=True)
        result_parent.symlink_to(outside, target_is_directory=True)
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(list(outside.iterdir()), [])
        popen.assert_not_called()

    def test_state_directory_symlink_is_rejected(self):
        outside = self.fx.base / "outside-state"
        outside.mkdir()
        self.fx.path(".b4pe").mkdir()
        self.fx.path(".b4pe/state").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(execution.SafetyError):
            self.fx.context()
        self.assertEqual(list(outside.iterdir()), [])

    def test_log_directory_symlink_is_rejected(self):
        outside = self.fx.base / "outside-log"
        outside.mkdir()
        self.fx.path(".b4pe").mkdir()
        self.fx.path(".b4pe/logs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(execution.SafetyError):
            self.fx.context()
        self.assertEqual(list(outside.iterdir()), [])

    def test_result_parent_symlink_swap_cannot_escape_dirfd(self):
        self.fx.write_inputs()
        context = self.fx.context()
        outside = self.fx.base / "outside-race"
        outside.mkdir()
        result = self.fx.path(self.fx.record["result_relpath"])
        moved_parent = result.parent.with_name(result.parent.name + "-trusted-inode")
        swapped = {"done": False}

        def swap_parent(_context, _temporary, final, _parent_fd):
            if final == self.fx.record["result_relpath"] and not swapped["done"]:
                swapped["done"] = True
                result.parent.rename(moved_parent)
                result.parent.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(execution, "_before_replace_hook", side_effect=swap_parent):
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertTrue(swapped["done"])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertNotEqual(self.fx.state()["current_status"], "succeeded")

    def test_inspector_reports_prepared_publication_without_recovery(self):
        self.fx.write_inputs()
        context = self.fx.context()
        real_write = execution._write_state
        injected = {"done": False}

        def fail_after_result(ctx, state):
            attempts = state.get("attempts", [])
            status = attempts[-1]["publication"]["publication_status"] if attempts else "none"
            if status == "result_published" and not injected["done"]:
                injected["done"] = True
                raise OSError("injected")
            return real_write(ctx, state)

        with mock.patch.object(execution, "_write_state", side_effect=fail_after_result):
            execution.execute_records([self.fx.record], context)
        before = self.fx.path(f".b4pe/state/{self.fx.record['case_id']}.json").read_bytes()
        report = inspect_execution.inspect_output(self.fx.output_root)
        after = self.fx.path(f".b4pe/state/{self.fx.record['case_id']}.json").read_bytes()
        self.assertEqual(report["publication_status_counts"]["prepared"], 1)
        self.assertEqual(before, after)

    def test_inspector_reports_snapshot_damage(self):
        self.fx.write_inputs()
        self.assertEqual(execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1)
        snapshot = self.fx.path(self.fx.state()["source_snapshot_relpath"])
        snapshot.chmod(0o600)
        snapshot.write_text("damaged\n", encoding="utf-8")
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["snapshot_sha_mismatches"], 1)

    def test_inspector_reports_summary_filename_mismatch(self):
        self.fx.write_inputs()
        self.assertEqual(execution.execute_records([self.fx.record], self.fx.context())["succeeded"], 1)
        summary = next(self.fx.path(".b4pe/summaries").glob("*.json"))
        summary.rename(summary.with_name("0" * 64 + ".json"))
        report = inspect_execution.inspect_output(self.fx.output_root)
        self.assertEqual(report["summary_sha_mismatches"], 1)

    def _external_snapshot_bytes(self, role):
        if role == "system":
            return (
                json.dumps(
                    {"mode": "snapshot_digest", "external": True},
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        if role == "simulator":
            return b"#!/bin/sh\nexit 97\n"
        return f"external {role} content\n".encode("utf-8")

    def _run_snapshot_namespace_attack(self, role, attack):
        self.fx.write_inputs({"mode": "snapshot_digest"})
        context = self.fx.context()
        captured_fds = []
        external_sha = {}

        def mutate_namespace(_record, current):
            opened = current["active_snapshot_execution"]
            captured_fds.extend(item["fd"] for item in opened.values())
            for descriptor in captured_fds:
                os.fstat(descriptor)
            provenance = current["active_provenance"]
            relative = provenance[f"{role}_snapshot_relpath"]
            snapshot = self.fx.path(relative)
            replacement = self._external_snapshot_bytes(role)
            external_sha["value"] = hashlib.sha256(replacement).hexdigest()
            outside = self.fx.base / f"outside-{role}-{attack}"
            outside.mkdir()
            external = outside / snapshot.name
            external.write_bytes(replacement)
            if role == "simulator":
                external.chmod(0o500)
            if attack == "parent_symlink":
                moved = snapshot.parent.with_name(
                    snapshot.parent.name + "-opened-inode"
                )
                snapshot.parent.rename(moved)
                snapshot.parent.symlink_to(outside, target_is_directory=True)
            elif attack == "file_regular":
                snapshot.rename(snapshot.with_name(snapshot.name + ".opened-inode"))
                snapshot.write_bytes(replacement)
            elif attack == "file_symlink":
                snapshot.rename(snapshot.with_name(snapshot.name + ".opened-inode"))
                snapshot.symlink_to(external)
            else:
                self.fail(f"unknown attack: {attack}")

        with mock.patch.object(
            execution, "_before_popen_hook", side_effect=mutate_namespace
        ):
            summary = execution.execute_records([self.fx.record], context)
        self.assertEqual(summary["succeeded"], 1)
        state = self.fx.state()
        payload = json.loads(
            self.fx.path(self.fx.record["result_relpath"]).read_text(
                encoding="utf-8"
            )
        )
        for current_role in execution.SNAPSHOT_ROLES:
            self.assertEqual(
                payload[f"{current_role}_sha256"],
                state[f"{current_role}_snapshot_sha256"],
            )
            self.assertEqual(
                state["attempts"][0]["snapshot_execution"][current_role][
                    "executed_snapshot_sha256"
                ],
                payload[f"{current_role}_sha256"],
            )
        self.assertEqual(
            payload["simulator_version"],
            "b4pe-fake-simulator-r2-file-fd",
        )
        self.assertNotEqual(
            external_sha["value"], state[f"{role}_snapshot_sha256"]
        )
        for descriptor in captured_fds:
            with self.assertRaises(OSError) as raised:
                os.fstat(descriptor)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_system_snapshot_parent_swap_uses_opened_file(self):
        self._run_snapshot_namespace_attack("system", "parent_symlink")

    def test_taskset_snapshot_parent_swap_uses_opened_file(self):
        self._run_snapshot_namespace_attack("taskset", "parent_symlink")

    def test_source_snapshot_parent_swap_uses_opened_file(self):
        self._run_snapshot_namespace_attack("source", "parent_symlink")

    def test_simulator_snapshot_parent_swap_uses_opened_file(self):
        self._run_snapshot_namespace_attack("simulator", "parent_symlink")

    def test_system_snapshot_filename_replacement_uses_opened_file(self):
        self._run_snapshot_namespace_attack("system", "file_regular")

    def test_simulator_snapshot_filename_symlink_uses_opened_file(self):
        self._run_snapshot_namespace_attack("simulator", "file_symlink")

    def test_fake_simulator_reports_actual_input_sha_and_version(self):
        self.fx.write_inputs({"mode": "snapshot_digest"})
        summary = execution.execute_records([self.fx.record], self.fx.context())
        self.assertEqual(summary["succeeded"], 1)
        state = self.fx.state()
        payload = json.loads(
            self.fx.path(self.fx.record["result_relpath"]).read_text(
                encoding="utf-8"
            )
        )
        for role in execution.SNAPSHOT_ROLES:
            self.assertEqual(
                payload[f"{role}_sha256"], state[f"{role}_snapshot_sha256"]
            )
        self.assertEqual(
            payload["simulator_version"],
            "b4pe-fake-simulator-r2-file-fd",
        )

    def test_proc_fd_unavailable_fails_without_fallback(self):
        self.fx.write_inputs()
        with mock.patch.object(
            execution, "PROC_FD_ROOT", "/definitely/missing/proc-fd"
        ), mock.patch.object(execution.subprocess, "Popen") as popen:
            summary = execution.execute_records(
                [self.fx.record], self.fx.context()
            )
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertFalse(self.fx.path(self.fx.record["result_relpath"]).exists())
        popen.assert_not_called()

    def test_popen_failure_closes_all_snapshot_file_fds(self):
        self.fx.write_inputs()
        captured = []

        def record_fds(_record, context):
            captured.extend(
                item["fd"]
                for item in context["active_snapshot_execution"].values()
            )

        with mock.patch.object(
            execution, "_before_popen_hook", side_effect=record_fds
        ), mock.patch.object(
            execution.subprocess, "Popen", side_effect=OSError("injected")
        ):
            summary = execution.execute_records(
                [self.fx.record], self.fx.context()
            )
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertEqual(len(captured), 4)
        for descriptor in captured:
            with self.assertRaises(OSError) as raised:
                os.fstat(descriptor)
            self.assertEqual(raised.exception.errno, errno.EBADF)


if __name__ == "__main__":
    unittest.main()
