import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import generate_manifest
import manifest_common as manifest


class ManifestGenerationTests(unittest.TestCase):
    def test_pilot_count(self):
        self.assertEqual(sum(1 for _ in manifest.iter_cases("pilot")), 2400)

    def test_formal_count(self):
        self.assertEqual(sum(1 for _ in manifest.iter_cases("formal_main")), 18000)

    def test_negative_count(self):
        self.assertEqual(sum(1 for _ in manifest.iter_cases("negative_control")), 5400)

    def test_all_count(self):
        self.assertEqual(sum(1 for _ in manifest.iter_cases("all")), 25800)

    def test_phase_algorithm_coverage_uses_identity_order(self):
        for phase in ("pilot", "formal_main", "negative_control"):
            expected = manifest.IDENTITY.RESOLUTION["phase_algorithms"][phase]
            iterator = manifest.iter_cases(phase)
            observed = [next(iterator)["algorithm"] for _ in expected]
            self.assertEqual(observed, expected)

    def test_repeated_generation_is_byte_identical(self):
        self.assertEqual(
            manifest.render_manifest("pilot"),
            manifest.render_manifest("pilot"),
        )

    def test_output_destination_does_not_change_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.jsonl"
            second = Path(temp_dir) / "nested" / "second.jsonl"
            generate_manifest.write_manifest("pilot", first)
            generate_manifest.write_manifest("pilot", second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_taskset_identity_is_decoupled(self):
        first = manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK")
        variants = (
            manifest.build_case("pilot", "0.3", 1, "1.15", "2", "ASAP-BLOCK"),
            manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ST-BLOCK"),
        )
        for changed in variants:
            self.assertEqual(changed["taskset_id"], first["taskset_id"])
            self.assertEqual(changed["taskset_seed"], first["taskset_seed"])

    def test_source_identity_is_decoupled_from_algorithm(self):
        first = manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK")
        changed = manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ST-BLOCK")
        self.assertEqual(changed["source_id"], first["source_id"])
        self.assertEqual(changed["source_seed"], first["source_seed"])

    def test_formal_negative_reuse_taskset_and_source(self):
        formal = manifest.build_case(
            "formal_main", "0.4", 37, "0.85", "2", "ASAP-BLOCK"
        )
        negative = manifest.build_case(
            "negative_control", "0.4", 37, "0.85", "1", "ASAP-BLOCK"
        )
        self.assertEqual(formal["taskset_id"], negative["taskset_id"])
        self.assertEqual(formal["taskset_seed"], negative["taskset_seed"])
        self.assertEqual(formal["source_id"], negative["source_id"])
        self.assertEqual(formal["source_seed"], negative["source_seed"])

    def test_case_id_changes_with_case_dimensions(self):
        baseline = manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK")
        variants = (
            manifest.build_case("pilot", "0.3", 1, "0.70", "2", "ASAP-BLOCK"),
            manifest.build_case("pilot", "0.3", 1, "0.70", "1", "ST-BLOCK"),
            manifest.build_case("formal_main", "0.3", 1, "0.70", "1", "ASAP-BLOCK"),
        )
        self.assertEqual(len({baseline["case_id"], *(v["case_id"] for v in variants)}), 4)

    def test_all_case_paths_are_canonical_relative(self):
        for record in manifest.iter_cases("pilot"):
            for field in (
                "taskset_artifact_relpath",
                "source_artifact_relpath",
                "system_config_artifact_relpath",
                "result_relpath",
            ):
                value = record[field]
                self.assertFalse(PurePosixPath(value).is_absolute())
                self.assertNotIn("..", PurePosixPath(value).parts)


if __name__ == "__main__":
    unittest.main()
