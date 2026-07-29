import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import admission_common as admission
import execution_common as execution
import manifest_common as manifest
import materialization_common as materialization


CANDIDATE_PATH = B4_DIR / "b4_pe_freeze_candidate_v4.json"
CHAIN_PATHS = (
    CANDIDATE_PATH,
    B4_DIR / "manifest_protocol_v4.json",
    B4_DIR / "base_pool_admission_protocol_v1.json",
    B4_DIR / "materialization_protocol_v1.json",
    B4_DIR / "pilot_authorization_v4.json",
    B4_DIR / "execution_protocol_v4.json",
)
EXPECTED_GOVERNANCE = {
    "formal_runs_authorized": False,
    "negative_control_runs_authorized": False,
    "paper_result_authorized": False,
    "pilot_runs_authorized": True,
}
HISTORICAL_IDENTITIES = {
    "b4_pe_freeze_candidate_v1.json":
        "d5d2e6cfe7751f15227cb93ca66b17d11455cf5dccbcd768aebafb8623822732",
    "b4_pe_freeze_candidate_v2.json":
        "31bb158c5d1312850478331a7beb6c6c2da4d74f9639c23672dd8a976396e8ef",
    "b4_pe_freeze_candidate_v3.json":
        "c30c74c971cb82f01d243f733e5276b04ff4e862d317fe501a235c55070712cf",
    "execution_protocol_v1.json":
        "74fd9ed742ad41dbedb66a5e7de2bbc796e746ae2efb207d2d456deed10cdd34",
    "execution_protocol_v2.json":
        "632b737b1c7cff9dd70eb7c091561be5ac7e5902333b4006c0b40faa5c9f3cfb",
    "execution_protocol_v3.json":
        "b76a44ac48c1721e4a0b2042a53d787c22b78a0ec017ea171d92534fd1d107ec",
    "manifest_protocol_v1.json":
        "e00a1fe5ccc4713a9b6b211dde8d6682919d0f599b16424deaf06661c17e148f",
    "manifest_protocol_v2.json":
        "4d1ead28d2b957ef0b8764f7148f2aab7643893f4134f8e56234bc913058ce90",
    "manifest_protocol_v3.json":
        "c51e774e74ad3ce9bb4d39bacfccb5a7c64e71750c6a0f12432c4ab70070603f",
}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


class V4RuntimeClosureBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = manifest.load_candidate_v4()
        cls.closure = cls.candidate["runtime_closure"]

    def test_candidate_code_is_bound_but_final_identity_is_unset(self):
        self.assertEqual(
            self.candidate["candidate_code_commit"],
            "681409e35012d2bc883045e4d10a048b36a6483f",
        )
        self.assertEqual(
            self.candidate["candidate_code_tree"],
            "266ecfdca0c1bc194e2ce77a295254893b9737ca",
        )
        self.assertEqual(self.candidate["freeze_status"], "candidate")
        self.assertEqual(
            self.candidate["runtime_binding_status"],
            "candidate_bound_pilot_authorized",
        )
        self.assertIsNone(self.candidate["final_code_commit"])
        self.assertIsNone(self.candidate["final_git_tag"])
        self.assertIsNone(self.candidate["formal_runtime_binary_path"])
        self.assertIsNone(self.candidate["formal_runtime_binary_sha256"])

    def test_runtime_artifact_identities_are_exact(self):
        expected = {
            "dual_python_launcher":
                "e000fd8bb4e12505b86abb7b33573d2d8a0ce4d3948fb1d801235bc0be5c6f25",
            "libcmdarg":
                "02aa859ea7eee6a5b3c3c6c32826656349ee629f19d5a86c245acfb44186c5fd",
            "libmetasim":
                "20734b7ffff7db8352593aa1c89f20716dbcec8462e638591e9855d20525e324",
            "libpython3_8":
                "d6b4470a33290dd9203b9a497b4fa9744e55ff63f59b788d75128973571a66a6",
            "librtsim":
                "f566e702435da6070059ff5ec1b47b7b8063e5081db1eb2351de57bc3f6245de",
            "simulator":
                "96004d1aec42cac73bea72d4fe0d5c2a5e814453bfeeb16d09026c4ff8746f7d",
        }
        self.assertEqual(
            {
                name: entry["sha256"]
                for name, entry in self.closure["artifacts"].items()
            },
            expected,
        )

    def test_stage2_evidence_and_tree_manifests_are_exact(self):
        evidence = self.closure["evidence"]
        expected = {
            "normalized_dynamic_dependency_manifest":
                "876abaa6b8812578c93ff12ac4a977f9da65240bb81f623e0233b57fcb8e9e3b",
            "python310_tree_manifest":
                "1e37cdfa1c0fd7a9ed4f7c0f650a363509530f06e5d609216c0392afc1993e99",
            "python38_tree_manifest":
                "6d903c9a25e20cfee4023ddc9bb163d5a1bfd6e14370cd6ade48d88e3ae1bfbe",
            "stage2a_runtime_execution_closure":
                "795533ed3ea3dadb950eef1dc1057be0a9efcdc7668706fe92752853322fdc91",
            "stage2b_supplemental_seal":
                "c3be2ef579f9650723237213dd778ac2ef57c804ad9ae5100787f6ea9eba9f60",
            "v1_aslr_defect_evidence":
                "054aef3c6fe36eed8291a88952c38c07fd03acfa5c084e400377aca499290ace",
        }
        self.assertEqual(
            {name: entry["sha256"] for name, entry in evidence.items()},
            expected,
        )
        self.assertTrue(all(
            entry["verification_status"] == "independently_verified"
            for entry in evidence.values()
        ))

    def test_external_evidence_filenames_match_generated_members(self):
        evidence = self.closure["evidence"]
        self.assertEqual(
            {
                name: entry["filename"]
                for name, entry in evidence.items()
            },
            {
                "normalized_dynamic_dependency_manifest":
                    "dynamic_dependencies.normalized.json",
                "python310_tree_manifest":
                    "python310_tree.normalized.jsonl",
                "python38_tree_manifest":
                    "python38_tree.normalized.jsonl",
                "stage2a_runtime_execution_closure":
                    "runtime_execution_closure_v1.json",
                "stage2b_supplemental_seal":
                    "pilot_deployment_runtime_supplemental_seal_v2.json",
                "v1_aslr_defect_evidence":
                    "prior_v1_aslr_defect.json",
            },
        )
        self.assertTrue(all("logical_path" not in entry for entry in evidence.values()))

    def test_only_container_documents_claim_schema_versions(self):
        evidence = self.closure["evidence"]
        self.assertEqual(
            {
                name: entry["schema_version"]
                for name, entry in evidence.items()
                if "schema_version" in entry
            },
            {
                "stage2a_runtime_execution_closure": 1,
                "stage2b_supplemental_seal": 2,
            },
        )
        self.assertEqual(
            {
                name: entry["serialization"]
                for name, entry in evidence.items()
                if "serialization" in entry
            },
            {
                "normalized_dynamic_dependency_manifest": "canonical_json",
                "python310_tree_manifest": "canonical_jsonl",
                "python38_tree_manifest": "canonical_jsonl",
                "v1_aslr_defect_evidence": "canonical_json",
            },
        )
        self.assertTrue(all(
            evidence[name]["container_evidence"]
            == "stage2b_supplemental_seal"
            for name in (
                "normalized_dynamic_dependency_manifest",
                "python310_tree_manifest",
                "python38_tree_manifest",
                "v1_aslr_defect_evidence",
            )
        ))

    def test_invented_evidence_filenames_are_absent(self):
        invented = (
            "runtime_" + "supplemental_seal_v2.json",
            "normalized_" + "dynamic_dependency_manifest_v2.json",
            "python38_" + "tree_manifest_v1.json",
            "python310_" + "tree_manifest_v1.json",
            "aslr_" + "defect_evidence_v1.json",
            "runtime_" + "supplemental_seal_v1.json",
            "dynamic_" + "dependency_manifest_v1.json",
        )
        evidence = self.closure["evidence"]
        bound_filenames = {
            entry["filename"]
            for entry in evidence.values()
        }
        bound_filenames.update(
            entry["supersedes"]["filename"]
            for entry in evidence.values()
            if "supersedes" in entry
        )
        for filename in invented:
            with self.subTest(filename=filename):
                self.assertNotIn(filename, bound_filenames)

    def test_v2_dependency_representation_supersedes_v1_without_identity_change(self):
        representation = self.closure[
            "deterministic_dependency_representation"
        ]
        self.assertEqual(
            representation,
            {
                "independent_normalizations_byte_identical": True,
                "pilot_authorization_effect": "none",
                "raw_aslr_addresses_stored": False,
                "raw_ldd_lines_stored": False,
                "supersedes_v1_nondeterministic_representation": True,
            },
        )
        evidence = self.closure["evidence"]
        self.assertEqual(
            evidence["stage2b_supplemental_seal"]["supersedes"]["filename"],
            "pilot_deployment_runtime_seal_v1.json",
        )
        self.assertEqual(
            evidence["stage2b_supplemental_seal"]["supersedes"]["sha256"],
            "1f9dd5b512b2318b0e4395c7253d7c5c3102caa45eb212cf75c80baf48e3ea24",
        )
        self.assertEqual(
            evidence["normalized_dynamic_dependency_manifest"][
                "supersedes"
            ]["filename"],
            "dynamic_dependency_manifest.jsonl",
        )
        self.assertEqual(
            evidence["normalized_dynamic_dependency_manifest"][
                "supersedes"
            ]["sha256"],
            "b259ea7727798fa1fc38319a73d16e0699b2d15408a09244a8b72a0ed039ee5f",
        )

    def test_complete_sha_reference_chain_loads_and_matches(self):
        manifest_protocol = manifest.load_manifest_protocol(
            manifest.MANIFEST_PROTOCOL_V4_PATH
        )
        admission_protocol = admission.load_protocol()
        materialization_protocol = (
            materialization.load_materialization_protocol()
        )
        execution_protocol = execution.load_execution_protocol(
            execution.EXECUTION_PROTOCOL_V4_PATH
        )
        authorization = execution.load_pilot_authorization_v4()
        self.assertEqual(
            manifest_protocol["candidate_v4_sha256"],
            _sha256(CANDIDATE_PATH),
        )
        self.assertEqual(
            admission_protocol["manifest_protocol_sha256"],
            _sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        )
        self.assertEqual(
            materialization_protocol[
                "base_pool_admission_protocol_sha256"
            ],
            _sha256(admission.PROTOCOL_PATH),
        )
        self.assertEqual(
            materialization_protocol["manifest_protocol_sha256"],
            _sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        )
        self.assertEqual(
            execution_protocol["candidate_v4_sha256"],
            _sha256(CANDIDATE_PATH),
        )
        self.assertEqual(
            execution_protocol["manifest_protocol_sha256"],
            _sha256(manifest.MANIFEST_PROTOCOL_V4_PATH),
        )
        self.assertEqual(
            execution_protocol["materialization_protocol_sha256"],
            _sha256(materialization.MATERIALIZATION_PROTOCOL_PATH),
        )
        self.assertEqual(
            authorization["candidate_sha256"],
            _sha256(CANDIDATE_PATH),
        )
        self.assertEqual(
            authorization["pilot_manifest_sha256"],
            "3af6a2b8764cf634d6e014e546a0d01d25a1d0a2caf05420d2c7ddf78dfe67d2",
        )
        self.assertEqual(
            execution_protocol["pilot_authorization_sha256"],
            _sha256(execution.PILOT_AUTHORIZATION_V4_PATH),
        )

    def test_only_v4_pilot_is_authorized_and_partial_execution_fails_closed(self):
        documents = [
            self.candidate,
            manifest.PROTOCOL_V4,
            admission.load_protocol(),
            materialization.load_materialization_protocol(),
            json.loads(
                execution.EXECUTION_PROTOCOL_V4_PATH.read_text(
                    encoding="utf-8"
                )
            ),
            execution.load_pilot_authorization_v4(),
        ]
        for document in documents:
            with self.subTest(protocol=document.get("protocol_name")):
                self.assertEqual(
                    {
                        name: document["governance"][name]
                        for name in EXPECTED_GOVERNANCE
                    },
                    EXPECTED_GOVERNANCE,
                )
        record = manifest.build_case(
            "pilot",
            "0.3",
            1,
            "0.85",
            "2",
            "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )
        with self.assertRaisesRegex(execution.SafetyError, "partial"):
            execution.execute_validated_cases(
                [record],
                "/does/not/matter",
                "/does/not/matter",
                "/does/not/matter",
            )

    def test_candidate_tampering_fails_closed(self):
        mutations = (
            ("candidate_code_commit", "0" * 40),
            ("candidate_code_tree", "0" * 40),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                changed = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
                changed[field] = value
                path = Path(tmp) / CANDIDATE_PATH.name
                path.write_bytes(_canonical_json_bytes(changed))
                with self.assertRaisesRegex(
                    manifest.ManifestError,
                    "identity mismatch",
                ):
                    manifest.load_candidate_v4(path)

    def test_binding_json_is_canonical_and_contains_no_host_or_aslr_data(self):
        forbidden_autodl_root = b"/root/" + b"autodl-tmp/"
        for path in CHAIN_PATHS:
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                document = json.loads(raw.decode("utf-8"))
                first = _canonical_json_bytes(document)
                second = _canonical_json_bytes(
                    json.loads(first.decode("utf-8"))
                )
                self.assertEqual(raw, first)
                self.assertEqual(first, second)
                self.assertNotIn(forbidden_autodl_root, raw)
                self.assertIsNone(re.search(rb"\(0x[0-9A-Fa-f]+\)", raw))

    def test_v1_v3_golden_files_are_byte_unchanged(self):
        for name, expected in HISTORICAL_IDENTITIES.items():
            with self.subTest(name=name):
                self.assertEqual(_sha256(B4_DIR / name), expected)


if __name__ == "__main__":
    unittest.main()
