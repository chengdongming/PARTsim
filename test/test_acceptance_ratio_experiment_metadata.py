import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock


os.environ.setdefault("MPLBACKEND", "Agg")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


class AcceptanceRatioExperimentMetadataTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def make_runner(self, **overrides):
        kwargs = {
            "output_dir": self.root / "output",
            "utilization_points": [0.5],
            "num_tasksets": 2,
            "task_n": 3,
            "task_p_min": 10,
            "task_p_max": 20,
            "simulation_time": 100,
            "battery_capacity": 20.0,
            "initial_energy_ratio": 1.0,
            "solar_start_time_ms": 12345,
            "use_real_solar_data": False,
            "system_cores": 2,
            "max_workers": 1,
            "enable_rta": True,
            "rta_horizon_ms": 100,
            "rta_assume_no_overflow": True,
            "rta_timeout": 7,
        }
        kwargs.update(overrides)
        return acceptance.ExperimentRunner(**kwargs)

    def test_seed_base_controls_taskset_seed(self):
        runner = self.make_runner(seed_base=9000)

        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run_mock:
            runner.generate_taskset(
                utilization=0.5,
                task_idx=1,
                system_config_file="config.yml",
            )

        command = run_mock.call_args.args[0]
        self.assertIn("--seed", command)
        self.assertEqual(command[command.index("--seed") + 1], "14001")
        self.assertEqual(command[command.index("-c") + 1], "2")
        self.assertEqual(command[command.index("--min-task-util") + 1], "0.01")
        self.assertEqual(command[command.index("--max-task-util") + 1], "0.8")
        self.assertEqual(command[command.index("--wcet-rounding") + 1], "floor")

    def test_generator_options_include_compensated_tolerance(self):
        runner = self.make_runner(
            wcet_rounding="compensated",
            actual_utilization_tolerance_total=0.01,
        )

        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run_mock:
            runner.generate_taskset(
                utilization=0.5,
                task_idx=0,
                system_config_file="config.yml",
            )

        command = run_mock.call_args.args[0]
        self.assertEqual(
            command[command.index("--wcet-rounding") + 1],
            "compensated",
        )
        self.assertEqual(
            command[
                command.index("--actual-utilization-tolerance-total") + 1
            ],
            "0.01",
        )

    def test_m_override_updates_system_config_numcpus(self):
        runner = self.make_runner(system_cores=5)
        config_path = Path(runner.modify_config(acceptance.ASAP_BLOCK_ALGORITHM))

        with config_path.open("r", encoding="utf-8") as handle:
            config = acceptance.yaml.safe_load(handle)
        self.assertEqual(config["cpu_islands"][0]["numcpus"], 5)

    def test_same_taskset_reused_across_all_9_schedulers(self):
        runner = self.make_runner(num_tasksets=1)
        generated = self.root / "taskset_u0.50_000.yml"
        generated.write_text("taskset: []\n", encoding="utf-8")

        with mock.patch.object(
            runner,
            "modify_config",
            side_effect=lambda algo: str(self.root / f"{algo}.yml"),
        ), mock.patch.object(
            runner,
            "generate_taskset",
            return_value=str(generated),
        ), mock.patch.object(
            acceptance, "ThreadPoolExecutor"
        ) as executor_cls:
            submitted = []

            class FakeFuture:
                def __init__(self, task):
                    self.task = task

                def result(self):
                    options = self.task[7]
                    return {
                        "algorithm": self.task[0],
                        "utilization": 0.5,
                        "task_idx": 0,
                        "task_file": str(generated),
                        "acceptance_ratio": 1.0,
                        "simulation_status": "accepted",
                        "simulation_error": None,
                        "rta_enabled": False,
                        "rta_error": None,
                        "taskset_id": options["taskset_id"],
                        "taskset_hash": options["taskset_hash"],
                        "config_id": options["config_id"],
                        "config_group_id": options["config_group_id"],
                    }

            class FakeExecutor:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def submit(self, _fn, task):
                    submitted.append(task)
                    return FakeFuture(task)

            executor_cls.return_value = FakeExecutor()
            with mock.patch.object(
                acceptance,
                "as_completed",
                side_effect=lambda futures: list(futures),
            ):
                runner.run_experiments()

        task_files = {task[2] for task in submitted}
        algorithms = {task[0] for task in submitted}
        self.assertEqual(task_files, {str(generated)})
        self.assertEqual(algorithms, set(acceptance.ALGORITHMS))

    def test_default_output_dir_is_unique_or_run_id_based(self):
        parser = argparse.ArgumentParser(add_help=False)
        acceptance.add_experiment_cli_args(parser)
        args = parser.parse_args([])

        self.assertNotEqual(args.output_dir, "acceptance_ratio_50tasks")
        self.assertTrue(
            any(token in args.output_dir for token in ["checkpoint", "run", "20"])
        )

    def test_taskset_semantic_hash_ignores_comments_but_preserves_task_order(self):
        first = self.root / 'first.yml'
        second = self.root / 'second.yml'
        first.write_text(
            '# generated: now\n# system: /tmp/a.yml\n'
            'resources: []\n'
            'taskset:\n'
            '  - name: task_b\n    iat: 20\n    runtime: 2\n'
            '    deadline: 20\n    code: [\"fixed(2, bzip2)\"]\n'
            '  - name: task_a\n    iat: 10\n    runtime: 1\n'
            '    code: [\"fixed(1, control)\"]\n',
            encoding='utf-8',
        )
        second.write_text(
            '# generated: later\n# system: /different/output/config.yml\n'
            'taskset:\n'
            '  - code: [\"fixed(1, control);\"]\n    runtime: 1\n'
            '    iat: 10\n    deadline: 10\n    name: task_a\n'
            '  - code: [\"fixed(2, bzip2);\"]\n    deadline: 20\n'
            '    name: task_b\n    runtime: 2\n    iat: 20\n'
            'resources: []\n',
            encoding='utf-8',
        )
        self.assertNotEqual(
            acceptance.taskset_file_hash(first),
            acceptance.taskset_file_hash(second),
        )
        self.assertNotEqual(
            acceptance.taskset_semantic_hash(first),
            acceptance.taskset_semantic_hash(second),
        )

        same_order = self.root / 'same-order.yml'
        same_order.write_text(
            '# different non-semantic comments and formatting\n'
            'taskset:\n'
            '  - code: ["fixed(2, bzip2);"]\n    runtime: 2\n'
            '    deadline: 20\n    iat: 20\n    name: task_b\n'
            '  - name: task_a\n    deadline: 10\n    iat: 10\n'
            '    runtime: 1\n    code: ["fixed(1, control);"]\n'
            'resources: []\n',
            encoding='utf-8',
        )
        self.assertEqual(
            acceptance.taskset_semantic_hash(first),
            acceptance.taskset_semantic_hash(same_order),
        )

    def test_taskset_semantic_hash_rejects_unknown_task_fields(self):
        source = self.root / 'unknown-task-field.yml'
        source.write_text(
            'resources: []\ntaskset:\n'
            '  - name: task_0\n    iat: 10\n    runtime: 1\n'
            '    deadline: 10\n    output_path: /tmp/not-semantic\n'
            '    code: ["fixed(1, control)"]\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(ValueError, 'unknown_task_field'):
            acceptance.taskset_semantic_hash(source)

    def test_resource_declaration_order_changes_semantic_hash(self):
        first = self.root / 'resources-first.yml'
        second = self.root / 'resources-second.yml'
        body = (
            'taskset:\n  - name: task_0\n    iat: 10\n'
            '    runtime: 1\n    deadline: 10\n'
            '    code: ["fixed(1, control)"]\n'
        )
        first.write_text(
            'resources:\n  - {name: A, initial_state: unlocked}\n'
            '  - {name: B, initial_state: unlocked}\n' + body,
            encoding='utf-8',
        )
        second.write_text(
            'resources:\n  - {name: B, initial_state: unlocked}\n'
            '  - {name: A, initial_state: unlocked}\n' + body,
            encoding='utf-8',
        )
        self.assertNotEqual(
            acceptance.taskset_semantic_hash(first),
            acceptance.taskset_semantic_hash(second),
        )

    def test_taskset_semantic_hash_changes_for_behavior_fields(self):
        base = self.root / 'semantic-base.yml'
        changed = self.root / 'semantic-changed.yml'
        base.write_text(
            'resources: []\ntaskset:\n'
            '  - name: task_0\n    iat: 10\n    runtime: 1\n'
            '    deadline: 8\n    ph: 1\n'
            '    code: [\"fixed(1, control)\"]\n',
            encoding='utf-8',
        )
        changed.write_text(
            base.read_text(encoding='utf-8').replace('deadline: 8', 'deadline: 9'),
            encoding='utf-8',
        )
        self.assertNotEqual(
            acceptance.taskset_semantic_hash(base),
            acceptance.taskset_semantic_hash(changed),
        )

    def test_rta_entrypoint_bytes_change_config_and_group_identity(self):
        tool = self.root / 'rta_tool.py'
        tool.write_text('print(\"a\")\n', encoding='utf-8')
        with mock.patch.object(acceptance, 'RTA_TOOL', str(tool)):
            first = self.make_runner(output_dir=self.root / 'rta-first')
            first_ids = (first.config_id(0.5), first.config_group_id(0.5))
            tool.write_text('print(\"b\")\n', encoding='utf-8')
            second = self.make_runner(output_dir=self.root / 'rta-second')
            second_ids = (second.config_id(0.5), second.config_group_id(0.5))
        self.assertNotEqual(first_ids[0], second_ids[0])
        self.assertNotEqual(first_ids[1], second_ids[1])

    def test_real_solar_config_uses_immutable_run_snapshot(self):
        solar = self.root / 'source.csv'
        solar.write_text('0,1\n', encoding='utf-8')
        template = self.root / 'system.yml'
        template.write_text(
            'cpu_islands:\n'
            '  - name: cpus\n    numcpus: 2\n    kernel:\n'
            '      scheduler: gpfp_asap_block\n'
            'energy_management:\n'
            '  initial_energy_ratio: 1\n  initial_energy: 1\n'
            '  max_energy: 1\n  time_of_day_ms: 0\n'
            '  base_harvesting_rate: 0\n  harvesting_scale: 1\n'
            '  day_of_year: 1\n  use_real_solar_data: true\n'
            '  solar_data_file: {}\n'.format(solar),
            encoding='utf-8',
        )
        with mock.patch.object(acceptance, 'CONFIG_TEMPLATE', str(template)):
            runner = self.make_runner(
                output_dir=self.root / 'solar-run',
                use_real_solar_data=True,
                enable_rta=False,
            )
            snapshot = Path(runner.solar_snapshot['snapshot_path'])
            self.assertEqual(snapshot.read_text(encoding='utf-8'), '0,1\n')
            solar.write_text('0,2\n', encoding='utf-8')
            config = Path(runner.modify_config(acceptance.ASAP_BLOCK_ALGORITHM))
            config_text = config.read_text(encoding='utf-8')
        self.assertIn(str(snapshot.resolve()), config_text)
        self.assertEqual(snapshot.read_text(encoding='utf-8'), '0,1\n')

    def test_existing_output_dir_requires_overwrite_flag(self):
        output_dir = self.root / "existing-output"
        output_dir.mkdir()
        (output_dir / "acceptance_ratio_data.csv").write_text(
            "old-results\n", encoding="utf-8"
        )

        parser = argparse.ArgumentParser(add_help=False)
        acceptance.add_experiment_cli_args(parser)
        args = parser.parse_args(["--output-dir", str(output_dir)])

        with self.assertRaises(SystemExit):
            acceptance.validate_output_dir_args(parser, args)

    def test_aggregate_csv_contains_reproducibility_columns(self):
        runner = self.make_runner()
        results = defaultdict(lambda: defaultdict(list))
        results[acceptance.ASAP_BLOCK_ALGORITHM][0.5] = [
            {
                "algorithm": acceptance.ASAP_BLOCK_ALGORITHM,
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "task_idx": 0,
                "taskset_id": "u0.50-000",
                "seed": 14000,
                "rta_enabled": True,
                "rta_status": "proven_under_assumptions",
            }
        ]

        frame = runner.aggregate_results(results)
        required_columns = {
            "algorithm",
            "algorithm_display_name",
            "normalized_utilization",
            "num_samples",
            "num_successful",
            "acceptance_ratio",
            "seed_base",
            "taskset_count",
            "core_count",
            "avg_actual_total_utilization",
            "avg_actual_normalized_utilization",
            "avg_utilization_error_total",
            "battery_capacity",
            "harvesting_profile",
            "simulation_num_accepted",
            "simulation_num_rejected",
            "simulation_num_timeout",
            "simulation_num_error",
            "rta_num_analyzed",
            "rta_num_proven",
            "rta_num_unproven",
            "rta_num_errors",
            "rta_proven_ratio",
            "rta_version",
            "avg_tightness",
            "tightness_num_samples",
        }
        self.assertTrue(required_columns.issubset(set(frame.columns)))

    def test_v20_4_raw_row_records_bound_response_and_tightness(self):
        runner = self.make_runner(enable_rta=True)
        row = runner._per_taskset_result_row(
            acceptance.ASAP_BLOCK_ALGORITHM,
            0.5,
            {
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "rta_enabled": True,
                "rta_version": "v20.4",
                "rta_status": "proven_under_assumptions",
                "rta_proven_under_assumptions": True,
                "rta_bound": 10.0,
                "rta_attempted": True,
                "rta_runtime_sec": 0.25,
                "rta_runtime_source": "subprocess_wall_clock_perf_counter",
                "rta_timed_out": False,
                "rta_timeout_sec": 7,
                "rta_profile_enabled": True,
                "rta_profile_task_time_sum_sec": 0.2,
                "rta_profile_task_count": 3,
                "simulated_response_time": 8.0,
                "tightness_values": [2.0, 3.0],
            },
        )

        self.assertEqual(row["rta_version"], "v20.4")
        self.assertTrue(row["rta_proven"])
        self.assertTrue(row["rta_schedulable"])
        self.assertTrue(row["sim_schedulable"])
        self.assertFalse(row["soundness_violation"])
        self.assertTrue(row["soundness_valid"])
        self.assertEqual(row["soundness_excluded_reason"], "")
        self.assertEqual(row["rta_response_time_bound"], 10.0)
        self.assertEqual(row["rta_response_bound"], 10.0)
        self.assertTrue(row["rta_attempted"])
        self.assertEqual(row["rta_runtime_sec"], 0.25)
        self.assertEqual(
            row["rta_runtime_source"],
            "subprocess_wall_clock_perf_counter",
        )
        self.assertFalse(row["rta_timed_out"])
        self.assertEqual(row["rta_timeout_sec"], 7)
        self.assertTrue(row["rta_profile_enabled"])
        self.assertEqual(row["rta_profile_task_time_sum_sec"], 0.2)
        self.assertEqual(row["rta_profile_task_count"], 3)
        self.assertEqual(row["simulated_response_time"], 8.0)
        self.assertEqual(row["observed_max_response_time"], 8.0)
        self.assertEqual(len(row["config_id"]), 64)
        int(row["config_id"], 16)
        self.assertEqual(row["tightness"], 1.25)
        self.assertEqual(row["target_normalized_utilization"], 0.5)
        self.assertEqual(row["target_total_utilization"], 1.0)
        self.assertEqual(row["task_util_min"], 0.01)
        self.assertEqual(row["task_util_max"], 0.8)
        self.assertEqual(row["wcet_rounding"], "floor")
        self.assertEqual(row["deadline_mode"], "implicit")
        self.assertEqual(row["actual_utilization_tolerance_total"], "")
        self.assertIn(
            "actual_utilization_tolerance_total",
            acceptance.PER_TASKSET_RESULT_FIELDS,
        )
        runtime_fields = {
            "rta_attempted",
            "rta_runtime_sec",
            "rta_runtime_source",
            "rta_timed_out",
            "rta_timeout_sec",
            "rta_profile_enabled",
            "rta_profile_task_time_sum_sec",
            "rta_profile_task_count",
        }
        self.assertTrue(
            runtime_fields.issubset(acceptance.PER_TASKSET_RESULT_FIELDS)
        )

    def test_failure_categories_are_aggregated_separately(self):
        runner = self.make_runner(enable_rta=False)
        results = defaultdict(lambda: defaultdict(list))
        results[acceptance.ASAP_BLOCK_ALGORITHM][0.5] = [
            {"acceptance_ratio": 1.0, "simulation_status": "accepted"},
            {"acceptance_ratio": 0.0, "simulation_status": "rejected"},
            {"acceptance_ratio": 0.0, "simulation_status": "simulation_timeout"},
            {"acceptance_ratio": 0.0, "simulation_status": "simulation_error"},
        ]

        row = runner.aggregate_results(results).iloc[0]
        self.assertEqual(row["simulation_num_accepted"], 1)
        self.assertEqual(row["simulation_num_rejected"], 1)
        self.assertEqual(row["simulation_num_timeout"], 1)
        self.assertEqual(row["simulation_num_error"], 1)

    def test_non_asap_block_rta_not_applicable_is_excluded_from_rta_aggregation(self):
        runner = self.make_runner(enable_rta=True)
        results = defaultdict(lambda: defaultdict(list))
        results["gpfp_asap_nonblock"][0.5] = [
            {
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "rta_enabled": False,
                "rta_status": "not_applicable",
            }
        ]

        row = runner.aggregate_results(results).iloc[0]
        self.assertEqual(row["rta_num_analyzed"], 0)
        self.assertEqual(row["rta_num_proven"], 0)
        self.assertEqual(row["rta_num_unproven"], 0)
        self.assertEqual(row["rta_num_errors"], 0)

    def test_tightness_only_counts_proved_asap_block_samples(self):
        runner = self.make_runner(enable_rta=True)
        results = defaultdict(lambda: defaultdict(list))
        results[acceptance.ASAP_BLOCK_ALGORITHM][0.5] = [
            {
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "rta_enabled": True,
                "rta_status": "proven_under_assumptions",
                "rta_report": {
                    "tasks": [
                        {
                            "task_name": "task_1",
                            "proven": True,
                            "response_time_bound": 10.0,
                        },
                        {
                            "task_name": "task_2",
                            "proven": True,
                            "response_time_bound": 6.0,
                        },
                    ]
                },
                "max_observed_response_times": {
                    "task_1": 8.0,
                    "task_2": 4.0,
                },
            },
            {
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "rta_enabled": True,
                "rta_status": "rta_unproven",
                "rta_bound": None,
                "simulated_response_time": 10.0,
            },
        ]

        row = runner.aggregate_results(results).iloc[0]
        self.assertEqual(row["tightness_num_samples"], 2)
        self.assertAlmostEqual(row["avg_tightness"], 1.375)

    def test_non_asap_block_tightness_is_na(self):
        runner = self.make_runner(enable_rta=True)
        results = defaultdict(lambda: defaultdict(list))
        results["gpfp_asap_nonblock"][0.5] = [
            {
                "acceptance_ratio": 1.0,
                "simulation_status": "accepted",
                "rta_enabled": False,
                "rta_status": "not_applicable",
                "rta_bound": None,
                "simulated_response_time": 10.0,
            }
        ]

        row = runner.aggregate_results(results).iloc[0]
        self.assertEqual(row["tightness_num_samples"], 0)
        self.assertTrue(row["avg_tightness"] != row["avg_tightness"])


if __name__ == "__main__":
    unittest.main()
