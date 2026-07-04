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

    def test_same_taskset_reused_across_all_9_schedulers(self):
        runner = self.make_runner(num_tasksets=1)
        generated = self.root / "taskset_u0.50_000.yml"

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
                def result(self):
                    return {
                        "algorithm": submitted[-1][0],
                        "utilization": 0.5,
                        "task_idx": 0,
                        "task_file": str(generated),
                        "acceptance_ratio": 1.0,
                        "simulation_error": None,
                        "rta_enabled": False,
                        "rta_error": None,
                    }

            class FakeExecutor:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def submit(self, _fn, task):
                    submitted.append(task)
                    return FakeFuture()

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
        self.assertEqual(row["simulated_response_time"], 8.0)
        self.assertEqual(row["observed_max_response_time"], 8.0)
        self.assertIn("|M=2|n=3|util=0.50|", row["config_id"])
        self.assertEqual(row["tightness"], 1.25)

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
