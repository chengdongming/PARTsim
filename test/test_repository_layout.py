"""Regression checks for physical file relocation and retained logical IDs."""
from pathlib import Path
import unittest

from experiments.common.paths import resolve_system_template, LEGACY_TEMPLATE_NAMES
from experiments.v9_3 import exact_energy
from experiments.v9_3 import config
from experiments.common import energy_manager, global_task_generator, solar_data_loader
from experiments.rta import asap_block_rta, asap_block_rta_v9_3

ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_root_is_clear_and_old_modules_are_importable_from_packages(self):
        self.assertFalse(list(ROOT.glob("*.py")))
        self.assertFalse(list(ROOT.glob("*.yml")))
        for module in (energy_manager, global_task_generator, solar_data_loader,
                       asap_block_rta, asap_block_rta_v9_3):
            self.assertTrue(Path(module.__file__).is_relative_to(ROOT / "experiments"))

    def test_template_aliases_and_legacy_contracts(self):
        for name in LEGACY_TEMPLATE_NAMES:
            physical = ROOT / "configs" / "templates" / name
            self.assertTrue(physical.is_file())
            self.assertEqual(resolve_system_template(name), physical)
            self.assertEqual(resolve_system_template(ROOT/name), physical)
            self.assertEqual(resolve_system_template(physical), physical)
        exact_energy.verify_theory_document(ROOT)
        self.assertFalse((ROOT / exact_energy.THEORY_DOCUMENT_PATH).exists())
        self.assertTrue((ROOT / "test/fixtures/legacy_schema/ASAP_BLOCK_experiment_schema_v1_3_10.yaml").is_file())
        self.assertTrue((ROOT / "test/fixtures/legacy_schema/ASAP_BLOCK_data_dictionary_v1_3_10.yaml").is_file())

    def test_legacy_config_uses_relocated_template_without_changing_identity_name(self):
        loaded = config.load_config(ROOT / "test/fixtures/legacy_rta4/v9_3_core1_smoke.yaml",
                                    expected_core="CORE-1")
        self.assertEqual(loaded["energy"]["service_curve"]["system_template"],
                         "system_config_unified_template.yml")


if __name__ == "__main__":
    unittest.main()
