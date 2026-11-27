"""Current source layout and public experiment input invariants."""
from pathlib import Path
from fractions import Fraction
import unittest

from experiments.common.paths import resolve_system_template, LEGACY_TEMPLATE_NAMES
from experiments.common import energy_manager, global_task_generator, solar_data_loader
from experiments.pipeline import system_model, task_model, config, exact_energy

ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_root_and_retired_families_are_clear(self):
        self.assertFalse(list(ROOT.glob("*.py")))
        self.assertFalse(list(ROOT.glob("*.yml")))
        for old in ("experiments/v9_3", "experiments/rta",
                    "experiments/legacy_contracts", "experiments/b4_priority_energy",
                    "test/fixtures/legacy_schema", "test/fixtures/legacy_rta4"):
            self.assertFalse((ROOT / old).exists(), old)
        self.assertFalse((ROOT / exact_energy.THEORY_DOCUMENT_PATH).exists())
        for module in (energy_manager, global_task_generator, solar_data_loader,
                       system_model, task_model):
            self.assertTrue(Path(module.__file__).is_relative_to(ROOT / "experiments"))

    def test_template_aliases(self):
        for name in LEGACY_TEMPLATE_NAMES:
            physical = resolve_system_template(name)
            self.assertTrue(physical.is_file())
            self.assertTrue(physical.is_relative_to(ROOT / "configs/templates"))
            self.assertEqual(resolve_system_template(ROOT/name), physical)
            self.assertEqual(resolve_system_template(physical), physical)
        self.assertEqual(resolve_system_template("v9_3_b4_priority_energy_system_template.yml").name,
                         "priority_energy_system_template.yml")

    def test_current_config_no_longer_requires_old_proof_document(self):
        loaded = config.load_config(ROOT / "test/fixtures/task_generation.yaml",
                                    expected_core="CORE-1")
        self.assertEqual(loaded["energy"]["service_curve"]["system_template"],
                         "system_config_unified_template.yml")
        self.assertEqual(len(exact_energy.THEORY_DOCUMENT_SHA256), 64)

    def test_exact_task_and_service_checks_remain(self):
        task = task_model.V93Task("t", 1, 2, 3, Fraction(1, 10))
        self.assertEqual(task.power, Fraction(1, 10))
        with self.assertRaises(task_model.V93InputError):
            task_model.V93Task("t", 1, 2, 3, 0.1)
        self.assertEqual(task_model.validate_service_curve_v9_3([0,1,2], 2),
                         (Fraction(0),Fraction(1),Fraction(2)))
        for bad in ([0,2,1], [0,-1,2], [0,0.1,2]):
            with self.assertRaises(task_model.V93Error):
                task_model.validate_service_curve_v9_3(bad, 2)


if __name__ == "__main__":
    unittest.main()
