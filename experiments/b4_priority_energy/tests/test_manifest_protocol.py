import json
import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import manifest_common as manifest


class ManifestProtocolTests(unittest.TestCase):
    def test_protocol_references_frozen_inputs(self):
        protocol = manifest.PROTOCOL
        self.assertEqual(
            protocol["identity_protocol_sha256"],
            manifest.file_sha256(manifest.IDENTITY_PROTOCOL_PATH),
        )
        self.assertEqual(
            protocol["frozen_document_sha256"],
            manifest.file_sha256(manifest.FROZEN_DOCUMENT_PATH),
        )
        self.assertEqual(
            protocol["system_template_sha256"],
            manifest.file_sha256(manifest.SYSTEM_TEMPLATE_PATH),
        )

    def test_protocol_does_not_copy_identity_derivation_rules(self):
        protocol = json.loads(
            manifest.MANIFEST_PROTOCOL_PATH.read_text(encoding="utf-8")
        )
        for forbidden in (
            "canonicalization",
            "seed_derivation",
            "id_derivation",
            "taskset_key",
            "source_key",
            "case_key",
        ):
            self.assertNotIn(forbidden, protocol)

    def test_phase_matrix_products_match_identity_counts(self):
        for phase, matrix in manifest.PROTOCOL["phase_matrix"].items():
            algorithms = manifest.IDENTITY.RESOLUTION["phase_algorithms"][phase]
            product = (
                len(matrix["utilization"])
                * len(matrix["lambda_E"])
                * len(matrix["rho_E"])
                * matrix["replicate_count"]
                * len(algorithms)
            )
            self.assertEqual(product, manifest.expected_phase_count(phase))

    def test_algorithm_cli_mapping_covers_identity_union(self):
        expected = {
            name
            for names in manifest.IDENTITY.RESOLUTION["phase_algorithms"].values()
            for name in names
        }
        mapping = manifest.PROTOCOL["algorithm_cli_mapping"]
        self.assertEqual(set(mapping), expected)
        self.assertEqual(len(mapping.values()), len(set(mapping.values())))

    def test_execution_paths_are_relative(self):
        plan = manifest.PROTOCOL["execution_plan"]
        manifest.validate_relative_path(plan["simulator_argv0"])
        manifest.validate_relative_path(plan["system_template_relpath"])
        for template in plan["path_templates"].values():
            value = template.format(
                algorithm_cli="gpfp_asap_block",
                phase="pilot",
                case_id="case-probe",
                taskset_id="ts-probe",
                source_id="src-probe",
            )
            manifest.validate_relative_path(value)

    def test_case_schema_contains_required_fields(self):
        required = {
            "schema_version",
            "protocol_name",
            "phase",
            "case_id",
            "taskset_id",
            "taskset_seed",
            "source_id",
            "source_seed",
            "taskset_pool",
            "replicate_index",
            "algorithm",
            "utilization",
            "lambda_E",
            "rho_E",
            "M",
            "task_count",
            "horizon_ms",
            "source_profile",
            "E0_rule",
            "Emax_rule",
            "alpha_rule",
            "frozen_document_sha256",
            "system_template_sha256",
            "identity_protocol_sha256",
            "base_commit",
            "taskset_artifact_relpath",
            "source_artifact_relpath",
            "result_relpath",
            "timeout_seconds",
            "retry_policy",
            "command_argv",
        }
        self.assertTrue(required <= set(manifest.PROTOCOL["manifest_case_fields"]))


if __name__ == "__main__":
    unittest.main()
