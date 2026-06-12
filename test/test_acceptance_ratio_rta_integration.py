import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("MPLBACKEND", "Agg")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import acceptance_ratio_test as acceptance


class AcceptanceRatioRTAIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config = self.root / "config_gpfp_asap_block.yml"
        self.tasks = self.root / "tasks.yml"
        self.config.write_text("cpu_islands: []\n", encoding="utf-8")
        self.tasks.write_text("taskset: []\n", encoding="utf-8")

    def make_runner(self, enable_rta=False):
        return acceptance.ExperimentRunner(
            output_dir=self.root / "output",
            utilization_points=[0.5],
            num_tasksets=1,
            task_n=1,
            task_p_min=10,
            task_p_max=20,
            simulation_time=100,
            battery_capacity=20.0,
            initial_energy_ratio=1.0,
            solar_start_time_ms=0,
            use_real_solar_data=False,
            system_cores=1,
            max_workers=1,
            enable_rta=enable_rta,
            rta_horizon_ms=100 if enable_rta else None,
            rta_assume_no_overflow=enable_rta,
            rta_timeout=7,
        )

    @staticmethod
    def proven_payload():
        return {
            "conditional": True,
            "assumptions": ["battery does not overflow"],
            "proven_under_assumptions": True,
            "tasks": [
                {
                    "task_name": "task_0",
                    "proven_under_assumptions": True,
                    "failure_reason": None,
                }
            ],
        }

    def worker_task(self, algorithm):
        return (
            algorithm,
            str(self.config),
            str(self.tasks),
            0,
            0.5,
            100,
            str(self.root),
            {
                "enable_rta": True,
                "horizon_ms": 100,
                "assume_no_overflow": True,
                "timeout": 7,
            },
        )

    def test_enable_rta_requires_explicit_horizon(self):
        parser = argparse.ArgumentParser(add_help=False)
        args = SimpleNamespace(
            enable_rta=True,
            rta_horizon_ms=None,
            rta_timeout=10,
        )
        with self.assertRaises(SystemExit):
            acceptance.validate_rta_cli_args(parser, args)

    def test_generator_command_uses_requested_system_config(self):
        runner = self.make_runner()
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run_mock:
            runner.generate_taskset(
                0.5,
                0,
                1234,
                system_config_file=str(self.config),
            )

        command = run_mock.call_args.args[0]
        self.assertIn("-s", command)
        self.assertEqual(command[command.index("-s") + 1], str(self.config))

    def test_non_asap_block_worker_does_not_call_rta(self):
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ), mock.patch.object(acceptance, "TraceParser") as parser_mock, \
                mock.patch.object(
                    acceptance, "run_asap_block_rta"
                ) as rta_mock:
            parser_mock.return_value.get_acceptance_ratio.return_value = 1.0
            result = acceptance.run_single_simulation_worker(
                self.worker_task("gpfp_asap_nonblock")
            )

        rta_mock.assert_not_called()
        self.assertEqual(result["acceptance_ratio"], 1.0)
        self.assertEqual(result["rta_status"], "not_applicable")
        self.assertFalse(result["rta_enabled"])

    def test_rta_disabled_asap_block_worker_preserves_legacy_acceptance(self):
        worker_task = list(self.worker_task(acceptance.ASAP_BLOCK_ALGORITHM))
        worker_task[-1] = {"enable_rta": False}
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ), mock.patch.object(acceptance, "TraceParser") as parser_mock, \
                mock.patch.object(
                    acceptance, "run_asap_block_rta"
                ) as rta_mock:
            parser_mock.return_value.get_acceptance_ratio.return_value = 1.0
            result = acceptance.run_single_simulation_worker(
                tuple(worker_task)
            )

        rta_mock.assert_not_called()
        self.assertEqual(result["acceptance_ratio"], 1.0)
        self.assertEqual(result["simulation_status"], "accepted")
        self.assertEqual(result["rta_status"], "disabled")
        self.assertFalse(result["rta_enabled"])

    def test_asap_block_proven_uses_same_config_and_records_hash(self):
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(self.proven_payload()),
            stderr="",
        )
        with mock.patch.object(
            acceptance.subprocess, "run", return_value=completed
        ) as run_mock:
            result = acceptance.run_asap_block_rta(
                acceptance.ASAP_BLOCK_ALGORITHM,
                str(self.config),
                str(self.tasks),
                horizon_ms=100,
                assume_no_overflow=True,
                timeout=7,
            )

        command = run_mock.call_args.args[0]
        self.assertEqual(
            command[command.index("--system") + 1], str(self.config)
        )
        self.assertEqual(
            command[command.index("--tasks") + 1], str(self.tasks)
        )
        self.assertEqual(command[command.index("--horizon-ms") + 1], "100")
        self.assertIn("--assume-no-overflow", command)
        self.assertIn("--json", command)
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 7)
        self.assertEqual(result["rta_status"], "proven_under_assumptions")
        self.assertTrue(result["rta_proven_under_assumptions"])
        self.assertEqual(
            result["rta_system_config"], str(self.config.resolve())
        )
        self.assertEqual(
            result["rta_system_config_hash"],
            acceptance.hash_file(self.config),
        )

    def test_worker_passes_same_config_to_simulator_and_rta(self):
        simulation = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        rta = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(self.proven_payload()),
            stderr="",
        )
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            side_effect=[simulation, rta],
        ) as run_mock, mock.patch.object(
            acceptance, "TraceParser"
        ) as parser_mock:
            parser_mock.return_value.get_acceptance_ratio.return_value = 1.0
            result = acceptance.run_single_simulation_worker(
                self.worker_task(acceptance.ASAP_BLOCK_ALGORITHM)
            )

        simulation_command = run_mock.call_args_list[0].args[0]
        rta_command = run_mock.call_args_list[1].args[0]
        self.assertEqual(simulation_command[1], str(self.config))
        self.assertEqual(
            rta_command[rta_command.index("--system") + 1],
            simulation_command[1],
        )
        self.assertEqual(result["rta_status"], "proven_under_assumptions")

    def test_missing_no_overflow_assumption_forces_unproven(self):
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(self.proven_payload()),
            stderr="",
        )
        with mock.patch.object(
            acceptance.subprocess, "run", return_value=completed
        ) as run_mock:
            result = acceptance.run_asap_block_rta(
                acceptance.ASAP_BLOCK_ALGORITHM,
                str(self.config),
                str(self.tasks),
                horizon_ms=100,
                assume_no_overflow=False,
                timeout=7,
            )

        command = run_mock.call_args.args[0]
        self.assertNotIn("--assume-no-overflow", command)
        self.assertEqual(result["rta_status"], "rta_unproven")
        self.assertFalse(result["rta_proven_under_assumptions"])
        self.assertIn("_analysis", result["rta_failure_reasons"])

    def test_rta_timeout_does_not_change_simulation_acceptance(self):
        rta_error = acceptance._base_rta_result(status="rta_error")
        rta_error.update({
            "rta_enabled": True,
            "rta_error": "RTA timed out after 7 seconds",
        })
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ), mock.patch.object(acceptance, "TraceParser") as parser_mock, \
                mock.patch.object(
                    acceptance,
                    "run_asap_block_rta",
                    return_value=rta_error,
                ):
            parser_mock.return_value.get_acceptance_ratio.return_value = 1.0
            result = acceptance.run_single_simulation_worker(
                self.worker_task(acceptance.ASAP_BLOCK_ALGORITHM)
            )

        self.assertEqual(result["acceptance_ratio"], 1.0)
        self.assertEqual(result["simulation_status"], "accepted")
        self.assertEqual(result["rta_status"], "rta_error")

    def test_rta_unproven_does_not_change_simulation_acceptance(self):
        unproven = acceptance._base_rta_result(status="rta_unproven")
        unproven.update({
            "rta_enabled": True,
            "rta_unproven_tasks": ["task_0"],
            "rta_failure_reasons": {"task_0": "unable to prove"},
        })
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ), mock.patch.object(acceptance, "TraceParser") as parser_mock, \
                mock.patch.object(
                    acceptance,
                    "run_asap_block_rta",
                    return_value=unproven,
                ):
            parser_mock.return_value.get_acceptance_ratio.return_value = 1.0
            result = acceptance.run_single_simulation_worker(
                self.worker_task(acceptance.ASAP_BLOCK_ALGORITHM)
            )

        self.assertEqual(result["acceptance_ratio"], 1.0)
        self.assertEqual(result["simulation_status"], "accepted")
        self.assertEqual(result["rta_status"], "rta_unproven")

    def test_rta_timeout_nonzero_exit_and_invalid_json_are_rta_errors(self):
        with mock.patch.object(
            acceptance.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["python3"], 7),
        ):
            timeout_result = acceptance.run_asap_block_rta(
                acceptance.ASAP_BLOCK_ALGORITHM,
                str(self.config),
                str(self.tasks),
                horizon_ms=100,
                assume_no_overflow=True,
                timeout=7,
            )
        self.assertEqual(timeout_result["rta_status"], "rta_error")
        self.assertIn("timed out", timeout_result["rta_error"])

        failed = subprocess.CompletedProcess(
            [], 3, stdout="", stderr="analysis failed"
        )
        with mock.patch.object(
            acceptance.subprocess, "run", return_value=failed
        ):
            failed_result = acceptance.run_asap_block_rta(
                acceptance.ASAP_BLOCK_ALGORITHM,
                str(self.config),
                str(self.tasks),
                horizon_ms=100,
                assume_no_overflow=True,
                timeout=7,
            )
        self.assertEqual(failed_result["rta_status"], "rta_error")
        self.assertIn("code 3", failed_result["rta_error"])

        invalid = subprocess.CompletedProcess(
            [], 0, stdout="{not-json", stderr=""
        )
        with mock.patch.object(
            acceptance.subprocess, "run", return_value=invalid
        ):
            invalid_result = acceptance.run_asap_block_rta(
                acceptance.ASAP_BLOCK_ALGORITHM,
                str(self.config),
                str(self.tasks),
                horizon_ms=100,
                assume_no_overflow=True,
                timeout=7,
            )
        self.assertEqual(invalid_result["rta_status"], "rta_error")
        self.assertTrue(invalid_result["rta_error"])

    def test_aggregate_adds_rta_metrics_without_changing_acceptance(self):
        runner = self.make_runner(enable_rta=True)
        results = defaultdict(lambda: defaultdict(list))
        results[acceptance.ASAP_BLOCK_ALGORITHM][0.5] = [
            {
                "acceptance_ratio": 1.0,
                "rta_enabled": True,
                "rta_status": "proven_under_assumptions",
            },
            {
                "acceptance_ratio": 1.0,
                "rta_enabled": True,
                "rta_status": "rta_unproven",
            },
            {
                "acceptance_ratio": 0.0,
                "rta_enabled": True,
                "rta_status": "rta_error",
            },
        ]

        row = runner.aggregate_results(results).iloc[0]
        self.assertAlmostEqual(row["acceptance_ratio"], 2.0 / 3.0)
        self.assertEqual(row["num_successful"], 2)
        self.assertEqual(row["rta_num_analyzed"], 2)
        self.assertEqual(row["rta_num_proven"], 1)
        self.assertEqual(row["rta_num_unproven"], 1)
        self.assertEqual(row["rta_num_errors"], 1)
        self.assertEqual(row["sim_success_rta_proven"], 1)
        self.assertEqual(row["sim_success_rta_unproven"], 1)

    def test_rta_disabled_keeps_legacy_aggregate_columns(self):
        runner = self.make_runner(enable_rta=False)
        results = defaultdict(lambda: defaultdict(list))
        results[acceptance.ASAP_BLOCK_ALGORITHM][0.5] = [1.0, 0.0]

        frame = runner.aggregate_results(results)
        self.assertEqual(
            list(frame.columns),
            [
                "algorithm",
                "normalized_utilization",
                "acceptance_ratio",
                "num_samples",
                "num_successful",
            ],
        )
        self.assertEqual(frame.iloc[0]["acceptance_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
