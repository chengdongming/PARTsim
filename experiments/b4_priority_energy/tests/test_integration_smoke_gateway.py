import contextlib
import copy
import hashlib
import io
import json
import shutil
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
import integration_smoke_common as smoke
import manifest_common as manifest
from test_execution_success import ExecutionFixture, FAKE_SOURCE


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SmokeFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="b4pe-i4b2a0-")
        self.base = Path(self.temporary.name)
        self.output_root = (self.base / "output").resolve()
        self.output_root.mkdir()
        self.simulator = (self.base / "fake_simulator.py").resolve()
        shutil.copyfile(FAKE_SOURCE, self.simulator)
        self.simulator.chmod(0o755)
        self.generator = (self.base / "generator.py").resolve()
        self.generator.write_text("# fake generator\n", encoding="utf-8")
        self.record_path = (self.base / "integration-smoke.json").resolve()
        self.semantic_hash = "a" * 64
        self.record = {
            "schema_version": "b4-pe-integration-smoke-v1",
            "record_type": "integration_smoke",
            "phase": "integration_smoke",
            "execution_scope": "single-real-case",
            "selected_case_count": 1,
            "campaign_started": False,
            "campaign_result_count": 0,
            "not_for_paper": True,
            "case_id": "smoke-gateway-one",
            "algorithm": "ASAP-BLOCK",
            "simulator_path": str(self.simulator),
            "output_root": str(self.output_root),
            "system_config_path": "integration-smoke/artifacts/system.json",
            "taskset_path": "integration-smoke/artifacts/taskset.yml",
            "source_artifact_path": "integration-smoke/artifacts/source.json",
            "result_relpath": "integration-smoke/results/smoke-gateway-one.txt",
            "timeout_seconds": 0.25,
            "retry_policy": {
                "initial_timeout_seconds": 0.25,
                "max_attempts": 2,
                "on_final_failure": "fail_closed",
                "retry_on": ["timeout"],
                "retry_timeout_seconds": 1.0,
            },
            "provenance": {
                "generator_path": str(self.generator),
                "generator_sha256": sha(self.generator.read_bytes()),
                "generator_argv": [str(self.generator), "--seed", "1"],
                "taskset_raw_sha256": sha(b"tasks: []\n"),
                "taskset_semantic_hash": self.semantic_hash,
                "system_config_sha256": "b" * 64,
                "source_artifact_sha256": sha(b'{"source":"fake"}\n'),
                "simulator_sha256": sha(self.simulator.read_bytes()),
            },
        }
        self.record["command_argv"] = [
            self.record["simulator_path"],
            self.record["system_config_path"],
            self.record["taskset_path"],
            "100",
            "-t",
            self.record["result_relpath"],
            "--run-id",
            self.record["case_id"],
            "--taskset-semantic-hash",
            self.semantic_hash,
        ]
        self.write_inputs()
        self.write_record()

    def cleanup(self):
        self.temporary.cleanup()

    def path(self, relative):
        return self.output_root.joinpath(*Path(relative).parts)

    def write_inputs(self, mode="rtsim-trace-contract", **extra):
        config = {"mode": mode, "result_text": "smoke result\n", **extra}
        values = {
            self.record["system_config_path"]: (json.dumps(config) + "\n").encode(),
            self.record["taskset_path"]: b"tasks: []\n",
            self.record["source_artifact_path"]: b'{"source":"fake"}\n',
        }
        for relative, data in values.items():
            path = self.path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def write_record(self, record=None):
        value = self.record if record is None else record
        self.record_path.write_text(smoke.compact_json(value) + "\n", encoding="utf-8")

    def run(self, extra=None):
        argv = ["--integration-smoke-record", str(self.record_path), "--execute"]
        if extra:
            argv.extend(extra)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = execute_manifest.main(argv)
        return status, stdout.getvalue(), stderr.getvalue()

    def state(self):
        path = self.path(f".b4pe/state/{self.record['case_id']}.json")
        return json.loads(path.read_text(encoding="utf-8"))


class IntegrationSmokeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.fx = SmokeFixture()

    def tearDown(self):
        self.fx.cleanup()

    def assert_rejected(self, mutation):
        record = copy.deepcopy(self.fx.record)
        mutation(record)
        self.fx.write_record(record)
        with self.assertRaises(smoke.IntegrationSmokeError):
            smoke.validate_integration_smoke_record(self.fx.record_path)

    def test_valid_single_record_normalises_for_common_kernel(self):
        envelope = smoke.validate_integration_smoke_record(self.fx.record_path)
        self.assertEqual(len(envelope["records"]), 1)
        case = envelope["records"][0]
        self.assertEqual(case["phase"], "integration_smoke")
        self.assertEqual(case["taskset_artifact_relpath"], self.fx.record["taskset_path"])
        self.assertEqual(case["command_argv"], self.fx.record["command_argv"])

    def test_two_records_are_rejected(self):
        line = smoke.compact_json(self.fx.record)
        self.fx.record_path.write_text(line + "\n" + line + "\n", encoding="utf-8")
        with self.assertRaises(smoke.IntegrationSmokeError):
            smoke.validate_integration_smoke_record(self.fx.record_path)

    def test_pilot_phase_is_rejected_by_smoke_validator(self):
        self.assert_rejected(lambda r: r.__setitem__("phase", "pilot"))

    def test_integration_phase_is_rejected_by_formal_validator(self):
        with self.assertRaises(manifest.ManifestError):
            manifest.validate_manifest(self.fx.record_path)

    def test_not_for_paper_false_is_rejected(self):
        self.assert_rejected(lambda r: r.__setitem__("not_for_paper", False))

    def test_campaign_started_true_is_rejected(self):
        self.assert_rejected(lambda r: r.__setitem__("campaign_started", True))

    def test_campaign_result_count_is_rejected(self):
        self.assert_rejected(lambda r: r.__setitem__("campaign_result_count", 1))

    def test_formal_campaign_result_paths_are_rejected(self):
        for fragment in ("results/pilot", "results/formal", "results/negative"):
            with self.subTest(fragment=fragment):
                def mutate(record, value=fragment):
                    old = record["result_relpath"]
                    record["result_relpath"] = f"{value}/case.txt"
                    record["command_argv"] = [
                        record["result_relpath"] if item == old else item
                        for item in record["command_argv"]
                    ]
                self.assert_rejected(mutate)

    def test_repository_output_root_is_rejected(self):
        self.assert_rejected(lambda r: r.__setitem__("output_root", str(REPO_ROOT)))

    def test_result_placeholder_missing_or_duplicate_is_rejected(self):
        def missing(record):
            record["command_argv"].remove(record["result_relpath"])
        self.assert_rejected(missing)
        self.fx.write_record()
        def duplicate(record):
            record["command_argv"].append(record["result_relpath"])
        self.assert_rejected(duplicate)

    def test_cli_sources_are_required_and_mutually_exclusive(self):
        parser = execute_manifest.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--execute"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "--manifest", "formal.jsonl",
                "--integration-smoke-record", "smoke.json",
                "--execute",
            ])

    def test_smoke_path_enters_common_kernel_and_not_formal_entry(self):
        summary = execution.new_summary("0" * 64, 1)
        with mock.patch.object(execute_manifest, "prepare_and_execute") as formal, mock.patch.object(
            execute_manifest, "execute_validated_cases", return_value=summary
        ) as common:
            self.assertEqual(self.fx.run()[0], 0)
        formal.assert_not_called()
        common.assert_called_once()

    def test_smoke_path_does_not_call_formal_validator(self):
        with mock.patch.object(manifest, "validate_manifest", side_effect=AssertionError):
            self.assertEqual(self.fx.run()[0], 0)

    def test_formal_path_does_not_call_smoke_validator(self):
        formal = ExecutionFixture()
        try:
            formal.write_inputs()
            with mock.patch.object(
                smoke, "validate_integration_smoke_record", side_effect=AssertionError
            ):
                self.assertEqual(formal.run_cli()[0], 0)
        finally:
            formal.cleanup()

    def test_formal_manifest_cli_regression(self):
        formal = ExecutionFixture()
        try:
            formal.write_inputs()
            status, output, errors = formal.run_cli()
            self.assertEqual(status, 0, errors)
            self.assertEqual(json.loads(output)["succeeded"], 1)
        finally:
            formal.cleanup()

    def test_fake_single_case_succeeds_with_committed_publication(self):
        status, output, errors = self.fx.run()
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)["succeeded"], 1)
        state = self.fx.state()
        self.assertEqual(state["phase"], "integration_smoke")
        self.assertEqual(state["current_status"], "succeeded")
        self.assertEqual(state["attempts"][0]["publication"]["publication_status"], "committed")

    def test_command_argv_is_not_augmented(self):
        real_popen = execution.subprocess.Popen
        observed = []
        def recording_popen(argv, **kwargs):
            observed.append(list(argv))
            return real_popen(argv, **kwargs)
        with mock.patch.object(execution.subprocess, "Popen", side_effect=recording_popen):
            self.assertEqual(self.fx.run()[0], 0)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(observed[0]), len(self.fx.record["command_argv"]))
        self.assertEqual(observed[0][-2:], ["--taskset-semantic-hash", self.fx.semantic_hash])

    def test_smoke_paths_do_not_create_campaign_outputs(self):
        self.assertEqual(self.fx.run()[0], 0)
        relative_files = {
            path.relative_to(self.fx.output_root).as_posix()
            for path in self.fx.output_root.rglob("*")
            if path.is_file()
        }
        self.assertFalse(any("results/pilot" in path for path in relative_files))
        self.assertFalse(any("results/formal" in path for path in relative_files))
        self.assertFalse(any("results/negative" in path for path in relative_files))

    def test_timeout_retry_reuses_i4b1_state_machine(self):
        self.fx.write_inputs(mode="first_timeout_then_success", sleep_seconds=2)
        self.assertEqual(self.fx.run()[0], 0)
        state = self.fx.state()
        self.assertEqual(state["attempt_count"], 2)
        self.assertEqual(state["attempts"][0]["termination_reason"], "timeout")
        self.assertEqual(state["current_status"], "succeeded")

    def test_production_gateway_has_no_arbitrary_validator_or_second_executor(self):
        gateway_source = (B4_DIR / "execute_manifest.py").read_text(encoding="utf-8")
        smoke_source = (B4_DIR / "integration_smoke_common.py").read_text(encoding="utf-8")
        for forbidden in ("importlib", "validator_callback", "validator_path", "os.environ"):
            self.assertNotIn(forbidden, gateway_source)
        self.assertNotIn("subprocess", smoke_source)
        self.assertNotIn("Popen", smoke_source)
        self.assertNotIn("os.system", smoke_source)

    def test_changed_paths_remain_below_experiment_directory(self):
        import subprocess
        changed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        self.assertTrue(changed)
        self.assertTrue(all(path.startswith("experiments/b4_priority_energy/") for path in changed))


if __name__ == "__main__":
    unittest.main()
