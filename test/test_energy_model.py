import os
import sys
import tempfile
import unittest

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from energy_manager import EnergyConfig, EnergyManager
from global_task_generator import EnergyAwareTaskGenerator


class SchedulerEnergyModelTest(unittest.TestCase):
    def write_config(self, energy_management, base_frequency=8100):
        config = {
            "cpu_islands": [
                {
                    "name": "island0",
                    "numcpus": 4,
                    "base_freq": base_frequency,
                }
            ],
            "energy_management": energy_management,
        }
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yml",
            delete=False,
        )
        with handle:
            yaml.safe_dump(config, handle)
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_generator_and_energy_manager_use_multiplicative_golden_case(self):
        config_path = self.write_config({
            "scheduler_energy_model": {
                "base_power": 0.5,
                "workload_coefficients": {
                    "bzip2": 1.2,
                    "control": 0.2,
                    "idle": 0.1,
                },
                "frequency_power_ratios": {
                    8100: 0.93,
                },
            },
        })

        generator = EnergyAwareTaskGenerator(system_config_path=config_path)
        energy_config = EnergyConfig(config_path)
        expected_per_tick = 0.5 * 1.2 * 0.93 * 0.001

        self.assertAlmostEqual(
            generator.calculate_energy(1, "bzip2", 8100),
            expected_per_tick,
        )
        self.assertAlmostEqual(
            energy_config.calculate_task_energy("bzip2", 1, 8100),
            expected_per_tick,
        )
        self.assertAlmostEqual(
            generator.calculate_energy(10, "bzip2", 8100),
            expected_per_tick * 10,
        )

    def test_scheduler_model_and_frequency_power_ratios_take_priority(self):
        config_path = self.write_config({
            "scheduler_energy_model": {
                "base_power": 0.4,
                "workload_coefficients": {
                    "control": 0.3,
                    "idle": 0.2,
                },
                "frequency_power_ratios": {8100: 0.8},
                "frequency_scaling": {8100: 0.2},
            },
            "consumption_model": {
                "base_power": 0.9,
                "workload_coefficients": {
                    "control": 0.9,
                    "idle": 0.9,
                },
                "frequency_scaling": {8100: 0.1},
            },
        })

        generator = EnergyAwareTaskGenerator(system_config_path=config_path)
        energy_config = EnergyConfig(config_path)
        expected = 0.4 * 0.3 * 0.8 * 0.001

        self.assertAlmostEqual(
            generator.calculate_energy(1, "control", 8100),
            expected,
        )
        self.assertAlmostEqual(
            energy_config.calculate_task_energy("control", 1, 8100),
            expected,
        )
        self.assertEqual(generator.get_frequency_ratio(8100), 0.8)
        self.assertEqual(energy_config.get_frequency_ratio(8100), 0.8)

    def test_consumption_model_and_frequency_scaling_remain_supported(self):
        config_path = self.write_config({
            "consumption_model": {
                "base_power": 0.25,
                "workload_coefficients": {
                    "control": 0.4,
                    "idle": 0.2,
                },
                "frequency_scaling": {8100: 0.5},
            },
        })

        generator = EnergyAwareTaskGenerator(system_config_path=config_path)
        energy_config = EnergyConfig(config_path)
        expected = 0.25 * 0.4 * 0.5 * 0.001

        self.assertAlmostEqual(
            generator.calculate_energy(1, "control", 8100),
            expected,
        )
        self.assertAlmostEqual(
            energy_config.calculate_task_energy("control", 1, 8100),
            expected,
        )

    def test_idle_and_control_defaults_match_cpp_config_manager(self):
        config_path = self.write_config({})
        generator = EnergyAwareTaskGenerator(system_config_path=config_path)
        energy_config = EnergyConfig(config_path)
        expected = 0.5 * 0.1 * 0.93 * 0.001

        for workload in ("idle", "control"):
            self.assertEqual(generator.power_coefficients[workload], 0.1)
            self.assertEqual(energy_config.power_coefficients[workload], 0.1)
            self.assertAlmostEqual(
                generator.calculate_energy(1, workload, 8100),
                expected,
            )
            self.assertAlmostEqual(
                energy_config.calculate_task_energy(workload, 1, 8100),
                expected,
            )

    def test_energy_bridge_export_uses_scheduler_model_values(self):
        config_path = self.write_config({
            "periodic_collection_interval_ms": 1,
            "scheduler_energy_model": {
                "base_power": 0.37,
                "workload_coefficients": {
                    "control": 0.42,
                    "idle": 0.17,
                },
                "frequency_power_ratios": {8100: 0.77},
            },
        })

        manager = EnergyManager(config_path, verbose=False)
        exported = manager.get_config_for_cpp()

        self.assertEqual(exported["base_frequency"], 8100)
        self.assertEqual(exported["base_power"], 0.37)
        self.assertEqual(exported["power_coefficients"]["control"], 0.42)
        self.assertEqual(exported["power_coefficients"]["idle"], 0.17)
        self.assertEqual(exported["frequency_power_ratios"][8100], 0.77)


if __name__ == "__main__":
    unittest.main()
