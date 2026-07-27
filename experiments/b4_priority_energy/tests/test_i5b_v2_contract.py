import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import integration_smoke_common as smoke
import manifest_common as manifest
import observability_validation as observability


V1_IDENTITIES = {
    "protocol_resolution_v1.json":
        "941d754e27e1cf599127550561a14a92f46e3605df26dca3c9a97e0325eecd93",
    "manifest_protocol_v1.json":
        "e00a1fe5ccc4713a9b6b211dde8d6682919d0f599b16424deaf06661c17e148f",
    "execution_protocol_v1.json":
        "74fd9ed742ad41dbedb66a5e7de2bbc796e746ae2efb207d2d456deed10cdd34",
    "integration_smoke_protocol_v1.json":
        "4b7e47cd0e89e31540cc30317f91e54a64d45efe63fca05629ee16e5d1aded85",
    "b4_pe_freeze_candidate_v1.json":
        "d5d2e6cfe7751f15227cb93ca66b17d11455cf5dccbcd768aebafb8623822732",
    "B4_PE_FREEZE_CANDIDATE_v1.md":
        "bee0916c5b3b73084dc5d90a4353029648d1265339c6f084b0c924e4990d44f7",
}


def valid_summary():
    tasks = []
    for rank in range(10):
        tasks.append(
            {
                "task_name": f"task_{rank}",
                "priority_rank": rank,
                "is_top4": rank < 4,
                "is_bottom6": rank >= 4,
                "released_jobs": 1,
                "completed_jobs": 1,
                "terminated_jobs": 0,
                "deadline_miss_jobs": 0,
                "unfinished_at_horizon_jobs": 0,
                "executed_core_ticks": 1,
                "completed_response_time_count": 1,
                "completed_response_time_sum_ms": 1,
                "completed_response_time_max_ms": 1,
            }
        )
    return {
        "trace_schema_version": 3,
        "observability_summary_contract_version": 1,
        "observability_summary_horizon_ms": 10,
        "mechanism_summary": {
            "bypass_opportunity_ticks": 0,
            "actual_bypass_ticks": 0,
            "low_priority_bypass_core_ticks": 0,
            "hp_dispatch_demand_ticks": 0,
            "hp_energy_blocked_ticks": 0,
            "hp_energy_blocked_job_ticks": 0,
            "observed_decision_ticks": 10,
        },
        "energy_summary": {
            "offered_energy_j": 0.5,
            "credited_energy_j": 0.5,
            "clipped_energy_j": 0.0,
            "consumed_energy_j": 0.25,
            "battery_min_j": 0.5,
            "battery_max_j": 0.75,
            "battery_final_j": 0.75,
            "battery_empty_ticks": 0,
            "battery_full_ticks": 0,
            "observed_energy_intervals": 10,
        },
        "per_task_summary": tasks,
    }


class I5BV2ContractTests(unittest.TestCase):
    def test_all_v1_bytes_remain_frozen(self):
        for name, expected in V1_IDENTITIES.items():
            observed = hashlib.sha256(
                (B4_DIR / name).read_bytes()
            ).hexdigest()
            self.assertEqual(observed, expected, name)

    def test_v2_protocols_bind_schema3_and_candidate_v1(self):
        self.assertEqual(manifest.PROTOCOL["trace_schema_version"], 3)
        self.assertEqual(execution.PROTOCOL["trace_schema_version"], 3)
        self.assertEqual(smoke.PROTOCOL["trace_schema_version"], 3)
        for protocol in (
            manifest.PROTOCOL,
            execution.PROTOCOL,
            smoke.PROTOCOL,
        ):
            self.assertEqual(
                protocol["candidate_v1_sha256"],
                V1_IDENTITIES["b4_pe_freeze_candidate_v1.json"],
            )
            self.assertEqual(
                protocol["observability_contract_sha256"],
                observability.CONTRACT_SHA256,
            )

    def test_manifest_v2_command_activates_schema3(self):
        case = manifest.build_case(
            "pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK"
        )
        self.assertEqual(case["result_relpath"].split(".")[-1], "json")
        self.assertEqual(case["trace_schema_version"], 3)
        self.assertEqual(case["summary_horizon_ms"], 30000)
        self.assertEqual(
            case["command_argv"][-3:],
            [
                "--b4-observability-summary",
                "--b4-summary-horizon",
                "30000",
            ],
        )
        historical = manifest.build_case(
            "pilot",
            "0.3",
            1,
            "0.70",
            "1",
            "ASAP-BLOCK",
            manifest.PROTOCOL_V1,
        )
        self.assertNotIn(
            "--b4-observability-summary",
            historical["command_argv"],
        )

    def test_candidate_v2_is_not_a_final_freeze(self):
        candidate_path = B4_DIR / "b4_pe_freeze_candidate_v2.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        self.assertEqual(candidate["freeze_status"], "candidate")
        self.assertIsNone(candidate["final_code_commit"])
        self.assertIsNone(candidate["final_git_tag"])
        self.assertIsNone(candidate["formal_runtime_binary_path"])
        self.assertIsNone(candidate["formal_runtime_binary_sha256"])
        self.assertTrue(
            candidate["governance"]["silent_changes_forbidden"]
        )
        self.assertTrue(
            candidate["governance"][
                "not_final_until_rta_integration"
            ]
        )
        for phase in (
            "pilot",
            "formal_main",
            "negative_control",
            "all",
        ):
            rendered = manifest.render_manifest(phase)
            self.assertEqual(
                hashlib.sha256(rendered).hexdigest(),
                candidate["manifest_v2_identities"][phase][
                    "sha256"
                ],
            )
            self.assertEqual(
                rendered.count(b"\n"),
                candidate["manifest_v2_identities"][phase][
                    "case_count"
                ],
            )

    def test_strict_summary_rejects_unknowns_and_taskset_rank_drift(self):
        document = valid_summary()
        ranks = {f"task_{rank}": rank for rank in range(10)}
        report = observability.validate_schema3_summary(
            document,
            expected_horizon_ms=10,
            initial_energy_j=0.5,
            capacity_j=1.0,
            processor_count=4,
            expected_task_ranks=ranks,
        )
        self.assertEqual(report["task_count"], 10)

        unknown = copy.deepcopy(document)
        unknown["energy_summary"]["unknown"] = 0
        with self.assertRaises(
            observability.ObservabilityValidationError
        ):
            observability.validate_schema3_summary(
                unknown,
                expected_horizon_ms=10,
                initial_energy_j=0.5,
                capacity_j=1.0,
                processor_count=4,
                expected_task_ranks=ranks,
            )

        wrong_rank = dict(ranks)
        wrong_rank["task_0"], wrong_rank["task_1"] = 1, 0
        with self.assertRaises(
            observability.ObservabilityValidationError
        ):
            observability.validate_schema3_summary(
                document,
                expected_horizon_ms=10,
                initial_energy_j=0.5,
                capacity_j=1.0,
                processor_count=4,
                expected_task_ranks=wrong_rank,
            )


if __name__ == "__main__":
    unittest.main()
