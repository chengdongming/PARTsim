import json
import sys
import tempfile
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import manifest_common as manifest


class ManifestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = list(manifest.iter_cases("pilot"))

    def changed(self, index=0, **updates):
        records = list(self.records)
        records[index] = dict(records[index])
        records[index].update(updates)
        return records

    def test_valid_pilot(self):
        self.assertIs(manifest.validate_records(self.records), self.records)

    def test_jsonl_parse_and_validate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pilot.jsonl"
            path.write_bytes(manifest.render_manifest("pilot"))
            self.assertEqual(len(manifest.validate_manifest(path)), 2400)

    def test_invalid_jsonl_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.jsonl"
            path.write_text("{bad}\n", encoding="utf-8")
            with self.assertRaisesRegex(manifest.ManifestError, "invalid JSONL"):
                manifest.parse_manifest(path)

    def test_absolute_path_fails(self):
        records = self.changed(taskset_artifact_relpath="/tmp/taskset.yml")
        with self.assertRaisesRegex(manifest.ManifestError, "must be relative"):
            manifest.validate_records(records)

    def test_parent_path_fails(self):
        records = self.changed(result_relpath="results/../escape.txt")
        with self.assertRaisesRegex(manifest.ManifestError, "parent traversal"):
            manifest.validate_records(records)

    def test_duplicate_case_fails_distinctly(self):
        records = list(self.records) + [dict(self.records[0])]
        with self.assertRaisesRegex(manifest.DuplicateCaseError, "duplicate"):
            manifest.validate_records(records)

    def test_seed_collision_fails_distinctly(self):
        records = self.changed(5, taskset_seed=self.records[0]["taskset_seed"])
        with self.assertRaisesRegex(manifest.SeedCollisionError, "seed collision"):
            manifest.validate_records(records)

    def test_id_collision_fails_distinctly(self):
        records = self.changed(5, taskset_id=self.records[0]["taskset_id"])
        with self.assertRaisesRegex(manifest.IDCollisionError, "ID collision"):
            manifest.validate_records(records)

    def test_deleted_algorithm_fails(self):
        records = list(self.records)
        records.pop(0)
        with self.assertRaisesRegex(manifest.ManifestError, "case count mismatch"):
            manifest.validate_records(records)

    def test_unknown_algorithm_fails(self):
        records = self.changed(algorithm="UNKNOWN")
        with self.assertRaisesRegex(manifest.ManifestError, "unknown algorithm"):
            manifest.validate_records(records)

    def test_parameter_outside_matrix_fails(self):
        records = self.changed(utilization="0.9")
        with self.assertRaisesRegex(manifest.ManifestError, "outside matrix"):
            manifest.validate_records(records)

    def test_sha_tamper_fails(self):
        records = self.changed(frozen_document_sha256="0" * 64)
        with self.assertRaisesRegex(manifest.ManifestError, "SHA|mismatch"):
            manifest.validate_records(records)

    def test_case_id_tamper_fails(self):
        records = self.changed(case_id="case-" + "0" * 64)
        with self.assertRaisesRegex(manifest.ManifestError, "case_id mismatch"):
            manifest.validate_records(records)

    def test_taskset_id_tamper_fails(self):
        records = self.changed(taskset_id="ts-" + "0" * 64)
        with self.assertRaisesRegex(manifest.ManifestError, "taskset_id mismatch"):
            manifest.validate_records(records)

    def test_taskset_seed_tamper_fails(self):
        records = self.changed(taskset_seed=self.records[0]["taskset_seed"] + 1)
        with self.assertRaisesRegex(manifest.ManifestError, "taskset_seed mismatch"):
            manifest.validate_records(records)

    def test_source_id_tamper_fails(self):
        records = self.changed(source_id="src-" + "0" * 64)
        with self.assertRaisesRegex(manifest.ManifestError, "source_id mismatch"):
            manifest.validate_records(records)

    def test_output_path_conflict_fails(self):
        records = self.changed(1, result_relpath=self.records[0]["result_relpath"])
        with self.assertRaisesRegex(manifest.OutputConflictError, "output path conflict"):
            manifest.validate_records(records)

    def test_command_argv_non_array_fails(self):
        records = self.changed(command_argv="build/rtsim/rtsim")
        with self.assertRaisesRegex(manifest.ManifestError, "string array"):
            manifest.validate_records(records)

    def test_command_argv_non_string_fails(self):
        records = self.changed(command_argv=["build/rtsim/rtsim", 1])
        with self.assertRaisesRegex(manifest.ManifestError, "string array"):
            manifest.validate_records(records)

    def test_unknown_field_fails(self):
        records = self.changed(unexpected="value")
        with self.assertRaisesRegex(manifest.ManifestError, "fields mismatch"):
            manifest.validate_records(records)

    def test_missing_field_fails(self):
        records = list(self.records)
        records[0] = dict(records[0])
        records[0].pop("case_id")
        with self.assertRaisesRegex(manifest.ManifestError, "fields mismatch"):
            manifest.validate_records(records)


if __name__ == "__main__":
    unittest.main()
