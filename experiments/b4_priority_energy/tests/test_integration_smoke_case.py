import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import integration_smoke_case as real_case
import integration_smoke_common as smoke


def raw_taskset_document():
    tasks = []
    for index in range(real_case.TASK_COUNT):
        tasks.append(
            {
                "name": f"task_{index}",
                "iat": 100,
                "runtime": 12,
                "deadline": 80,
                "params": (
                    "period=100,wcet=12,arrival_offset=0,workload=hash"
                ),
                "code": ["fixed(12, hash)"],
            }
        )
    return {"metadata": {"num_tasks": 10}, "taskset": tasks}


class RealSmokeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="b4pe-i4b2a-case-")
        self.base = Path(self.temporary.name)
        self.output_root = self.base / "output"
        self.output_root.mkdir()
        self.raw = real_case._artifact_path(
            self.output_root, real_case.RAW_TASKSET_RELPATH
        )
        self.taskset = real_case._artifact_path(
            self.output_root, real_case.TASKSET_RELPATH
        )
        self.system = real_case._artifact_path(
            self.output_root, real_case.SYSTEM_RELPATH
        )
        self.source = real_case._artifact_path(
            self.output_root, real_case.SOURCE_RELPATH
        )
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text(
            yaml.safe_dump(raw_taskset_document(), sort_keys=False)
            + "# byte-preservation-marker\n",
            encoding="utf-8",
        )
        self.energy = real_case.materialize_taskset(self.raw, self.taskset)
        real_case.render_system_and_source(
            self.system, self.source, self.energy
        )
        self.simulator = self.base / "rtsim"
        self.simulator.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.simulator.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def make_record(self):
        semantic_hash = real_case.formal_semantic_hash(self.taskset)
        command = real_case.generator_argv(self.raw)
        record = real_case.build_record(
            self.output_root,
            self.simulator,
            command,
            real_case.file_sha256(self.raw),
            semantic_hash,
        )
        record_path = self.base / "record.json"
        real_case.write_record(record_path, record)
        return record_path, record

    def test_generator_argv_is_the_frozen_public_cli(self):
        command = real_case.generator_argv(
            self.base / "generated.yml", python_executable="/usr/bin/python3"
        )
        self.assertEqual(command[:2], [
            "/usr/bin/python3", str(real_case.TASK_GENERATOR_PATH)
        ])
        expected_pairs = {
            "--num-tasks": "10",
            "--utilization": "1.2",
            "--min-period": "40",
            "--max-period": "200",
            "--cpus": "4",
            "--seed": "424242",
            "--min-task-util": "0.01",
            "--max-task-util": "0.45",
            "--wcet-rounding": "compensated",
            "--actual-utilization-tolerance-total": "0.01",
            "--task-workload-candidate": "hash",
        }
        for option, value in expected_pairs.items():
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertIn("--constrained-deadlines", command)
        self.assertIn("--no-arrival-offset", command)

    def test_semantic_hash_calls_only_the_formal_function(self):
        expected = "a" * 64
        with mock.patch.object(
            real_case.acceptance,
            "taskset_semantic_hash",
            return_value=expected,
        ) as formal:
            self.assertEqual(
                real_case.formal_semantic_hash(self.taskset), expected
            )
        formal.assert_called_once_with(self.taskset)

    def test_system_and_source_render_the_frozen_single_source(self):
        template = real_case.SYSTEM_TEMPLATE_PATH.read_text(encoding="utf-8")
        rendered = self.system.read_text(encoding="utf-8")
        system = yaml.safe_load(self.system.read_text(encoding="utf-8"))
        descriptor = json.loads(self.source.read_text(encoding="utf-8"))
        expected = template.replace(
            real_case.SYSTEM_PRIORITY_ENERGY_PLACEHOLDER,
            """priority_energy:
  enabled: true
  profile_id: b4_pe_three_stage_v1
  alpha_w: {}
  horizon_ms: 30000
  tick_ms: 1
""".format(repr(float(self.energy["alpha_w"]))),
        ).replace(
            real_case.SYSTEM_ENERGY_BOUNDS_PLACEHOLDER,
            """  initial_energy: {}
  max_energy: {}
""".format(
                repr(float(self.energy["E0_j"])),
                repr(float(self.energy["Emax_j"])),
            ),
        ).replace(
            real_case.SYSTEM_LEGACY_SOURCE_PLACEHOLDER,
            "",
        )
        self.assertEqual(rendered, expected)
        self.assertIn(real_case.SYSTEM_INLINE_VOLTS_LINE, rendered)
        self.assertNotIn("\n    volts:\n", rendered)
        unchanged_fragment = """    freqs: [7000, 7500, 8000, 8100, 8200, 8300, 8400, 8500, 9000, 9500, 10000, 10500]
    base_freq: 9000

    power_model: energy_aware_model
    speed_model: energy_aware_model
"""
        self.assertIn(unchanged_fragment, template)
        self.assertIn(unchanged_fragment, rendered)
        self.assertNotIn(
            real_case.SYSTEM_PRIORITY_ENERGY_PLACEHOLDER,
            rendered,
        )
        self.assertNotIn(
            real_case.SYSTEM_ENERGY_BOUNDS_PLACEHOLDER,
            rendered,
        )
        self.assertNotIn(
            real_case.SYSTEM_LEGACY_SOURCE_PLACEHOLDER,
            rendered,
        )
        system_info = self.system.lstat()
        self.assertTrue(stat.S_ISREG(system_info.st_mode))
        self.assertGreater(system_info.st_size, 0)
        with mock.patch.object(
            real_case.yaml,
            "safe_dump",
            side_effect=AssertionError("system renderer called YAML dump"),
        ) as safe_dump:
            real_case.render_system_and_source(
                self.base / "system-no-dump.yml",
                self.base / "source-no-dump.json",
                self.energy,
            )
        safe_dump.assert_not_called()
        self.assertEqual(self.raw.read_bytes(), self.taskset.read_bytes())
        self.assertEqual(
            real_case.file_sha256(self.raw),
            real_case.file_sha256(self.taskset),
        )
        self.assertEqual(
            real_case.formal_semantic_hash(self.raw),
            real_case.formal_semantic_hash(self.taskset),
        )
        self.assertEqual(
            system["cpu_islands"][0]["kernel"]["scheduler"],
            "gpfp_asap_block",
        )
        self.assertEqual(system["cpu_islands"][0]["numcpus"], 4)
        self.assertTrue(system["priority_energy"]["enabled"])
        self.assertEqual(
            system["priority_energy"]["profile_id"],
            "b4_pe_three_stage_v1",
        )
        self.assertEqual(
            system["priority_energy"]["alpha_w"],
            float(self.energy["alpha_w"]),
        )
        self.assertEqual(
            system["energy_management"]["initial_energy"],
            float(self.energy["E0_j"]),
        )
        self.assertEqual(
            system["energy_management"]["max_energy"],
            float(self.energy["Emax_j"]),
        )
        self.assertNotIn("harvesting", system)
        self.assertFalse(
            real_case.LEGACY_SOURCE_FIELDS.intersection(
                system["energy_management"]
            )
        )
        self.assertEqual(descriptor["source"]["kind"], "scaled_piecewise")
        self.assertEqual(
            descriptor["source"]["segments"],
            [
                {"start_time_ms": 0, "end_time_ms": 5000, "multiplier": 1.0},
                {"start_time_ms": 5000, "end_time_ms": 15000, "multiplier": 0.2},
                {"start_time_ms": 15000, "end_time_ms": 30000, "multiplier": 1.0},
            ],
        )
        self.assertGreater(descriptor["E0_j"], 0)
        self.assertEqual(descriptor["Emax_j"], 2 * descriptor["E0_j"])

    def test_smoke_record_command_contains_the_semantic_hash(self):
        record_path, record = self.make_record()
        envelope = smoke.validate_integration_smoke_record(record_path)
        self.assertEqual(len(envelope["records"]), 1)
        self.assertEqual(record["phase"], "integration_smoke")
        self.assertTrue(record["case_id"].startswith("smoke-"))
        self.assertEqual(
            record["command_argv"][2],
            real_case.TASKSET_RELPATH,
        )
        self.assertEqual(
            record["command_argv"][-2:],
            [
                "--taskset-semantic-hash",
                record["provenance"]["taskset_semantic_hash"],
            ],
        )
        self.assertTrue(
            record["result_relpath"].startswith(
                "integration-smoke/results/"
            )
        )

    def test_execute_argv_only_substitutes_transport_paths(self):
        _record_path, record = self.make_record()
        snapshots = {
            "simulator": {"proc_fd_path": "/proc/self/fd/10"},
            "system": {"proc_fd_path": "/proc/self/fd/11"},
            "taskset": {"proc_fd_path": "/proc/self/fd/12"},
        }
        normalised = {
            **record,
            "system_config_artifact_relpath": record["system_config_path"],
            "taskset_artifact_relpath": record["taskset_path"],
        }
        with mock.patch.object(
            execution,
            "_proc_fd_child_path",
            return_value="/proc/self/fd/13/trace.json",
        ):
            actual = execution.build_execution_argv(
                normalised, {}, 1, 13, "trace.json", snapshots
            )
        self.assertEqual(len(actual), len(record["command_argv"]))
        self.assertEqual(
            actual[-2:],
            record["command_argv"][-2:],
        )
        self.assertEqual(actual[3:5], ["1000", "-t"])

    def test_result_parser_checks_identity_tasks_horizon_and_energy(self):
        semantic_hash = "b" * 64
        events = [
            {
                "time": 0,
                "event_type": "arrival",
                "task_name": f"task_{index}",
                "current_energy_mJ": 1.0,
                "total_consumed_mJ": 0.0,
            }
            for index in range(10)
        ]
        events.append(
            {
                "time": 1,
                "event_type": "harvest",
                "offered_harvest_mJ": 1.0,
                "actual_harvest_mJ": 0.75,
                "clipped_harvest_mJ": 0.25,
                "current_energy_mJ": 1.75,
            }
        )
        document = {
            "events": events,
            "run_id": "smoke-result",
            "taskset_semantic_hash": semantic_hash,
            "configured_scheduler": "gpfp_asap_block",
            "expected_simulation_horizon_ms": 1000,
            "observed_simulation_end_ms": 1000,
            "simulation_completed": True,
            "simulation_completion_reason": "reached_horizon",
        }
        report = real_case.validate_result_document(
            document, "smoke-result", semantic_hash, 1.0
        )
        self.assertEqual(report["task_count"], 10)
        self.assertTrue(report["battery_bounds_valid"])
        self.assertTrue(report["harvest_relation_valid_when_present"])

    def test_preflight_closes_artifact_and_provenance_hashes(self):
        record_path, _record = self.make_record()
        report = real_case.preflight(record_path)
        self.assertTrue(report["record_validated"])
        self.assertTrue(report["raw_materialized_sha_match"])
        self.assertEqual(
            report["raw_taskset_semantic_hash"],
            report["materialized_taskset_semantic_hash"],
        )
        self.source.write_text('{"tampered":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            real_case.RealSmokeCaseError, "source SHA mismatch"
        ):
            real_case.preflight(record_path)


if __name__ == "__main__":
    unittest.main()
