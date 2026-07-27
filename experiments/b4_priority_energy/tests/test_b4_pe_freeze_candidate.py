import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
B4_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B4_DIRECTORY))

import audit_manifest
import audit_results
import execute_manifest
import generate_manifest
import inspect_execution
import run_manifest
import validate_manifest


JSON_PATH = B4_DIRECTORY / "b4_pe_freeze_candidate_v1.json"
MARKDOWN_PATH = B4_DIRECTORY / "B4_PE_FREEZE_CANDIDATE_v1.md"
TEST_PATH = Path(__file__).resolve()
ALLOWED_CHANGED_PATHS = {
    "experiments/b4_priority_energy/b4_pe_freeze_candidate_v1.json",
    "experiments/b4_priority_energy/B4_PE_FREEZE_CANDIDATE_v1.md",
    "experiments/b4_priority_energy/tests/test_b4_pe_freeze_candidate.py",
}
EXPECTED_CANDIDATE_COMMIT = "d0339f40d4dac9277d69878c5d4f57003cbd48c4"
EXPECTED_MANIFESTS = {
    "all": (25800, "b3adfb138d72611c5a4013a523c1c38ab7d40edbda533239ba02b617be407497"),
    "formal_main": (18000, "5b27761ab556455dd94cfbb5c63725b16bfa126b4c38871b0b8b8d8ae801c777"),
    "negative_control": (5400, "690827b07c1320da843e77f06e9dbff638c24a4af43e20d7405f6f32ff5039fb"),
    "pilot": (2400, "cadfdcfea8876c17e47087e98406613104d3456f2685bf50dd19c109fd41f30b"),
}
EXPECTED_DOCUMENT_IDENTITY_MIGRATION = {
    "authorization_scope": "B4-PE R2/master integration",
    "authorized": True,
    "current_sha256": (
        "5e168664d9ce2062bf2418d2280195124c08b1311d1de1e280d20822965c0581"
    ),
    "document_path": (
        "docs/experiments/ASAP_BLOCK_B4_priority_energy_v5_2_frozen.md"
    ),
    "exact_reason": "removed two trailing spaces from line 2",
    "freeze_status": "candidate",
    "master_integration_commit": (
        "46ac0ece34eacbd5178e292c16de961359a5c440"
    ),
    "migration_scope": "R2/master integration only",
    "previous_identity_commit": (
        "8b09e37483eb2df6ce22621761f06433f2519663"
    ),
    "previous_sha256": (
        "0fee308839f2097664a63a21f8806128c868b1016fab2712e67892356961be52"
    ),
    "scientific_contract_change": False,
    "semantic_change": False,
    "silent_changes_forbidden": True,
    "source_pr": 58,
}
PILOT_ALGORITHMS = [
    "gpfp_asap_block",
    "gpfp_asap_nonblock",
    "gpfp_asap_sync",
    "gpfp_alap_block",
    "gpfp_st_block",
]
FORMAL_ALGORITHMS = [
    "gpfp_asap_block",
    "gpfp_asap_nonblock",
    "gpfp_asap_sync",
    "gpfp_alap_block",
    "gpfp_alap_nonblock",
    "gpfp_alap_sync",
    "gpfp_st_block",
    "gpfp_st_nonblock",
    "gpfp_st_sync",
]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)


class FreezeCandidateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_json = JSON_PATH.read_text(encoding="utf-8")
        cls.document = json.loads(cls.raw_json)
        cls.markdown = MARKDOWN_PATH.read_text(encoding="utf-8")
        cls.markdown_flat = " ".join(cls.markdown.split())

    def test_json_is_parseable_and_schema_is_v1(self):
        self.assertIsInstance(self.document, dict)
        self.assertEqual(self.document["schema_version"], 1)

    def test_freeze_is_a_candidate(self):
        self.assertEqual(self.document["freeze_status"], "candidate")
        self.assertTrue(self.document["not_final_until_rta_integration"])

    def test_authorized_document_identity_migration_is_exact(self):
        self.assertEqual(
            self.document["identity_migrations"],
            [EXPECTED_DOCUMENT_IDENTITY_MIGRATION],
        )
        self.assertTrue(self.document["governance"]["silent_changes_forbidden"])

    def test_final_identity_fields_are_unset(self):
        self.assertIsNone(self.document["final_code_commit"])
        self.assertIsNone(self.document["final_git_tag"])
        self.assertIsNone(self.document["runtime_binary"]["formal_runtime_binary_path"])
        self.assertIsNone(self.document["runtime_binary"]["formal_runtime_binary_sha256"])

    def test_candidate_commit_is_exact(self):
        self.assertEqual(self.document["candidate_code_commit"], EXPECTED_CANDIDATE_COMMIT)

    def test_all_repository_frozen_inputs_exist(self):
        for entry in self.document["frozen_inputs"].values():
            self.assertTrue((REPOSITORY_ROOT / entry["path"]).is_file(), entry["path"])

    def test_repository_frozen_input_hashes_match(self):
        for entry in self.document["frozen_inputs"].values():
            self.assertEqual(_sha256(REPOSITORY_ROOT / entry["path"]), entry["sha256"])

    def test_verified_local_binary_hash_matches(self):
        binary = self.document["runtime_binary"]
        path = Path(binary["verified_local_binary_path"])
        self.assertTrue(path.is_file())
        self.assertEqual(_sha256(path), binary["verified_binary_sha256"])

    def test_manifest_hashes_and_case_counts_are_frozen(self):
        for phase, (count, digest) in EXPECTED_MANIFESTS.items():
            self.assertEqual(self.document["manifests"][phase]["case_count"], count)
            self.assertEqual(self.document["manifests"][phase]["sha256"], digest)

    def test_manifest_reuse_counts_are_frozen(self):
        self.assertEqual(
            self.document["manifests"]["reuse"],
            {
                "formal_negative_shared_sources": 600,
                "formal_negative_shared_tasksets": 300,
                "unique_sources": 2240,
                "unique_tasksets": 560,
            },
        )

    def test_algorithm_order_matches_each_phase(self):
        manifests = self.document["manifests"]
        self.assertEqual(manifests["pilot"]["algorithm_order"], PILOT_ALGORITHMS)
        self.assertEqual(manifests["formal_main"]["algorithm_order"], FORMAL_ALGORITHMS)
        self.assertEqual(manifests["negative_control"]["algorithm_order"], FORMAL_ALGORITHMS)

    def test_total_request_count_is_phase_sum(self):
        manifests = self.document["manifests"]
        phase_sum = sum(
            manifests[name]["case_count"]
            for name in ("pilot", "formal_main", "negative_control")
        )
        self.assertEqual(phase_sum, manifests["all"]["case_count"])
        self.assertEqual(phase_sum, 25800)

    def test_platform_and_source_contract_are_typed_and_exact(self):
        platform = self.document["platform_and_tasks"]
        source = self.document["source_contract"]
        self.assertEqual((platform["processors"], platform["task_count"]), (4, 10))
        self.assertEqual(platform["formal_normalized_utilization"], [0.2, 0.3, 0.4, 0.5, 0.6])
        self.assertEqual(source["source_type"], "scaled_piecewise")
        self.assertEqual(source["runtime_profile"], "b4_pe_three_stage_v1")
        self.assertEqual(source["discrete_increment_count"], 30000)
        self.assertEqual(source["integral_equivalent_ms"], 22000)

    def test_energy_rules_are_declared_uniquely_recoverable(self):
        derivation = self.document["energy_derivation"]
        self.assertTrue(derivation["uniquely_recoverable"])
        self.assertEqual(derivation["rho_reference"], 2)
        self.assertEqual(derivation["rules"]["E0_j"], "E_burst_ref_j")
        self.assertEqual(derivation["rules"]["Emax_j"], "2*E_burst_ref_j")
        self.assertIn("lambda_E", derivation["rules"]["alpha_w"])
        for reference in derivation["authoritative_references"]:
            self.assertTrue((REPOSITORY_ROOT / reference["path"]).is_file())

    def test_energy_inputs_use_actual_task_yaml_fields_and_exact_rm_order(self):
        derivation = self.document["energy_derivation"]
        fields = derivation["input_fields"]
        self.assertEqual(fields["C_i"], "task.runtime")
        self.assertEqual(fields["T_i"], "task.iat")
        self.assertEqual(
            fields["O_i"],
            "integer parsed from task.params.arrival_offset",
        )
        self.assertNotIn("runtime_ms", self.raw_json)
        self.assertNotIn("period_ms", self.raw_json)
        self.assertNotIn("arrival_offset_ms", self.raw_json)
        self.assertEqual(
            derivation["rm_priority_order"],
            {
                "T_i_field": "task.iat",
                "primary_order": "T_i ascending",
                "sort_key": "(T_i, task_id)",
                "task_id_source": "integer suffix in the frozen task.name form task_<task_id>",
                "tie_break": "task_id ascending when T_i is equal",
            },
        )

    def test_directory_contract_is_placeholder_based(self):
        directory = self.document["directory_contract"]
        self.assertEqual(directory["root_placeholder"], "<experiment-root>")
        self.assertTrue(all(path.startswith("<experiment-root>/") for path in directory["layout"]))
        self.assertTrue(directory["formal_output_outside_repository"])
        self.assertFalse(directory["repository_source_tree_may_hold_formal_results"])

    def test_command_options_parse_with_current_cli(self):
        parsers = {
            "audit_manifest.py": audit_manifest.build_parser,
            "audit_results.py": audit_results.build_parser,
            "execute_manifest.py": execute_manifest.build_parser,
            "generate_manifest.py": generate_manifest.build_parser,
            "inspect_execution.py": inspect_execution.build_parser,
            "run_manifest.py": run_manifest.build_parser,
            "validate_manifest.py": validate_manifest.build_parser,
        }
        for command in self.document["commands"]:
            if command["tool"] == "sha256sum":
                continue
            with self.subTest(command=command["id"]):
                self.assertEqual(command["argv"][0], "python3")
                self.assertEqual(Path(command["argv"][1]).name, command["tool"])
                parsers[command["tool"]]().parse_args(command["argv"][2:])

    def test_json_has_no_timestamp_or_temporary_path(self):
        self.assertNotIn("/tmp", self.raw_json)
        self.assertNotIn("timestamp", self.raw_json.lower())
        absolute_strings = [value for value in _all_strings(self.document) if value.startswith("/")]
        self.assertEqual(
            absolute_strings,
            [self.document["runtime_binary"]["verified_local_binary_path"]],
        )

    def test_markdown_contains_every_frozen_sha(self):
        sha_values = [entry["sha256"] for entry in self.document["frozen_inputs"].values()]
        sha_values.append(self.document["runtime_binary"]["verified_binary_sha256"])
        sha_values.extend(
            self.document["manifests"][phase]["sha256"]
            for phase in ("pilot", "formal_main", "negative_control", "all")
        )
        for digest in sha_values:
            self.assertIn(digest, self.markdown)

    def test_markdown_excludes_smoke_from_paper_data(self):
        self.assertIn("Integration-smoke output is not paper data", self.markdown_flat)
        self.assertIn("only interoperability and pairing fairness", self.markdown_flat)
        self.assertIn("do not support any performance conclusion", self.markdown_flat)

    def test_markdown_defers_final_identity_until_i6(self):
        self.assertIn("only after I6 RTA integration", self.markdown_flat)
        self.assertIn("not the final paper-code freeze point", self.markdown_flat)

    def test_markdown_matches_actual_task_fields_and_rm_order(self):
        for field in (
            "task.runtime",
            "task.iat",
            "task.params.arrival_offset",
            "(T_i, task_id)",
        ):
            self.assertIn(field, self.markdown)
        for forbidden in ("runtime_ms", "period_ms", "arrival_offset_ms"):
            self.assertNotIn(forbidden, self.markdown)

    def test_json_serialization_is_stable(self):
        canonical = json.dumps(
            self.document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        self.assertEqual(self.raw_json, canonical)

    def test_formal_run_gate_has_exact_order(self):
        gate = self.document["formal_run_gate"]
        self.assertEqual([entry["order"] for entry in gate], list(range(1, 17)))
        self.assertEqual(len(gate), 16)

    def test_formal_requires_autodl_binary_and_passing_pilot_audit(self):
        gate = self.document["formal_run_gate"]
        steps = " ".join(entry["step"] for entry in gate)
        self.assertIn("AutoDL formal-run environment", steps)
        self.assertIn("AutoDL compiler", steps)
        self.assertIn("formal runtime binary path and SHA", steps)
        self.assertIn("Pilot result inspect and result audit", steps)
        self.assertIn("infrastructure_failure_count=0", steps)
        self.assertIn("audit_failure_count=0", steps)
        self.assertIn("overall_pass=true", steps)
        self.assertIn("stop and do not start Formal", steps)
        pilot_gate = next(
            index for index, entry in enumerate(gate)
            if "require infrastructure_failure_count=0" in entry["step"]
        )
        formal_start = next(
            index for index, entry in enumerate(gate)
            if "run the 18000-case Formal phase" in entry["step"]
        )
        self.assertLess(pilot_gate, formal_start)
        self.assertIn(
            "Formal may start only after Pilot result inspect and result audit",
            self.document["failure_policy"]["pilot_audit_gate"],
        )

    def test_markdown_matches_autodl_and_pilot_audit_gate(self):
        for statement in (
            "AutoDL formal run environment",
            "formal runtime binary path and SHA",
            "infrastructure_failure_count=0",
            "audit_failure_count=0",
            "overall_pass=true",
            "stop and do not start Formal",
            "Only after it passes",
        ):
            self.assertIn(statement, self.markdown_flat)

    def test_failure_policy_keeps_scheduling_outcomes_only(self):
        policy = self.document["failure_policy"]
        self.assertEqual(policy["infrastructure_failure_statistics_policy"], "exclude_from_statistics")
        self.assertEqual(policy["audit_failure_statistics_policy"], "exclude_from_statistics")
        self.assertEqual(policy["scheduling_outcome_statistics_policy"], "retain_as_algorithm_result")

    def test_change_scope_contains_no_production_file(self):
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        changed_paths = {line[3:] for line in status if line}
        self.assertTrue(changed_paths.issubset(ALLOWED_CHANGED_PATHS), changed_paths)
        self.assertIn(JSON_PATH.relative_to(REPOSITORY_ROOT).as_posix(), ALLOWED_CHANGED_PATHS)
        self.assertIn(MARKDOWN_PATH.relative_to(REPOSITORY_ROOT).as_posix(), ALLOWED_CHANGED_PATHS)
        self.assertIn(TEST_PATH.relative_to(REPOSITORY_ROOT).as_posix(), ALLOWED_CHANGED_PATHS)


if __name__ == "__main__":
    unittest.main()
