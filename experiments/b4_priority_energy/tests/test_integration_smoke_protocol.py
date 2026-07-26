import json
import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import integration_smoke_common as smoke


class IntegrationSmokeProtocolTests(unittest.TestCase):
    def test_protocol_is_live_and_self_consistent(self):
        self.assertEqual(smoke.PROTOCOL, smoke._load_protocol())
        self.assertRegex(smoke.PROTOCOL_SHA256, r"^[0-9a-f]{64}$")

    def test_fixed_identity_is_non_campaign(self):
        self.assertEqual(
            smoke.PROTOCOL["fixed_identity"],
            {
                "campaign_result_count": 0,
                "campaign_started": False,
                "execution_scope": "single-real-case",
                "not_for_paper": True,
                "phase": "integration_smoke",
                "selected_case_count": 1,
            },
        )

    def test_record_fields_include_execution_and_provenance_inputs(self):
        fields = set(smoke.PROTOCOL["record_fields"])
        self.assertTrue(
            {
                "case_id",
                "algorithm",
                "command_argv",
                "simulator_path",
                "output_root",
                "system_config_path",
                "taskset_path",
                "source_artifact_path",
                "result_relpath",
                "timeout_seconds",
                "retry_policy",
                "provenance",
            }
            <= fields
        )

    def test_gateway_has_no_validator_injection_contract(self):
        rules = smoke.PROTOCOL["gateway_rules"]
        self.assertFalse(rules["production_validator_injection"])
        self.assertFalse(rules["formal_validator_for_smoke"])
        self.assertFalse(rules["smoke_validator_for_formal"])

    def test_retry_contract_is_timeout_only_and_bounded(self):
        self.assertEqual(smoke.PROTOCOL["retry_rules"]["allowed_retry_on"], ["timeout"])
        self.assertEqual(smoke.PROTOCOL["retry_rules"]["max_attempts"], 2)
        self.assertEqual(smoke.PROTOCOL["retry_rules"]["on_final_failure"], "fail_closed")

    def test_result_namespace_excludes_campaign_paths(self):
        rules = smoke.PROTOCOL["path_rules"]
        self.assertEqual(rules["result_prefix"], "integration-smoke/results/")
        self.assertEqual(
            rules["campaign_path_fragments_forbidden"],
            ["results/pilot", "results/formal", "results/negative"],
        )

    def test_protocol_json_has_no_dynamic_validator_field(self):
        payload = json.dumps(smoke.PROTOCOL, sort_keys=True)
        for forbidden in ("validator_path", "validator_module", "validator_callback"):
            self.assertNotIn(forbidden, payload)


if __name__ == "__main__":
    unittest.main()
