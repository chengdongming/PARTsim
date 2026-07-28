import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import admission_common as admission
import manifest_common as manifest


class BasePoolAdmissionTests(unittest.TestCase):
    @staticmethod
    def _record():
        return manifest.build_case(
            "pilot", "0.3", 1, "0.85", "1", "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )

    @staticmethod
    def _simulator(root):
        path = root / "simulator"
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_cpu_only_system_is_exact_four_core_gfp_rm_without_energy_gate(self):
        payload = admission.render_cpu_only_system()
        document = yaml.safe_load(payload.decode("utf-8"))
        self.assertIs(document["priority_energy"]["enabled"], False)
        island = document["cpu_islands"][0]
        self.assertEqual(island["numcpus"], 4)
        self.assertEqual(
            island["kernel"],
            {
                "scheduler": "gpfp_asap_block",
                "task_placement": "global",
            },
        )
        self.assertEqual(
            document["energy_management"]["initial_energy"],
            admission.UNBOUNDED_ENERGY_J,
        )
        self.assertEqual(
            document["energy_management"]["max_energy"],
            admission.UNBOUNDED_ENERGY_J,
        )

    def test_accepted_inventory_binds_all_required_cpu_gate_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = self._record()
            with mock.patch.object(
                admission, "_run_cpu_gate", return_value=(1234, 0)
            ) as gate:
                inventory = admission.admit_records(
                    [record], root, "a" * 64, self._simulator(root)
                )
            gate.assert_called_once()
            entry = inventory["base_tasksets"][0]
            self.assertEqual(
                set(entry),
                {
                    "taskset_pool", "utilization", "replicate_index",
                    "taskset_seed", "taskset_id", "base_taskset_path",
                    "base_taskset_sha256", "base_semantic_hash",
                    "cpu_only_simulator_sha256",
                    "cpu_only_system_config_sha256", "horizon_ms",
                    "adjudicable_job_count", "deadline_miss_count",
                    "admission_status",
                },
            )
            self.assertEqual(entry["admission_status"], "accepted")
            self.assertEqual(entry["deadline_miss_count"], 0)
            self.assertEqual(entry["adjudicable_job_count"], 1234)
            self.assertTrue((root / entry["base_taskset_path"]).is_file())
            inventory_path = root / record[
                "base_pool_admission_inventory_relpath"
            ]
            self.assertEqual(
                inventory_path.read_bytes(),
                (
                    json.dumps(
                        inventory,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            )

    def test_nonzero_deadline_miss_is_recorded_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = self._record()
            with mock.patch.object(
                admission, "_run_cpu_gate", return_value=(1234, 1)
            ), self.assertRaisesRegex(
                admission.AdmissionError, "failed CPU-only admission"
            ):
                admission.admit_records(
                    [record], root, "b" * 64, self._simulator(root)
                )
            inventory = json.loads(
                (root / record[
                    "base_pool_admission_inventory_relpath"
                ]).read_text(encoding="utf-8")
            )
            entry = inventory["base_tasksets"][0]
            self.assertEqual(entry["admission_status"], "rejected")
            self.assertEqual(entry["deadline_miss_count"], 1)
            self.assertFalse((root / entry["base_taskset_path"]).exists())


if __name__ == "__main__":
    unittest.main()
