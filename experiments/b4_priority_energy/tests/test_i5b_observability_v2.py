import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import analysis_common as analysis
import execution_common as execution
import integration_smoke_common as smoke
import manifest_common as manifest
import observability_validation as observability


MECHANISM_FIELDS = (
    "bypass_opportunity_ticks",
    "actual_bypass_ticks",
    "low_priority_bypass_core_ticks",
    "hp_dispatch_demand_ticks",
    "hp_energy_blocked_ticks",
    "hp_energy_blocked_job_ticks",
    "observed_decision_ticks",
    "sync_batch_evaluation_ticks",
    "sync_batch_reject_ticks",
    "alap_deferral_opportunity_ticks",
    "positive_slack_deferral_ticks",
    "st_charging_opportunity_ticks",
    "st_slack_charging_wait_ticks",
)

FROZEN_V1_SHA256 = {
    "observability_summary_contract_v1.json":
        "1817fdbf795f878769a32cf3f1e4ba09dd2ce589137e1d2e487a8df866b7c7f6",
    "analysis_contract_v1.json":
        "15945810a1f5c9792056c88f2ed5e733b75cbdea6635c41c0cdda4163ba67f1f",
    "protocol_resolution_v1.json":
        "941d754e27e1cf599127550561a14a92f46e3605df26dca3c9a97e0325eecd93",
}


def taskset():
    return {
        "taskset": [
            {
                "name": f"task_{rank}",
                "iat": 100 + rank,
                "deadline": 100 + rank,
                "ph": 0,
                "params": (
                    f"period={100 + rank},wcet=1,"
                    "arrival_offset=0,workload=hash"
                ),
            }
            for rank in range(10)
        ]
    }


def summary_v2(horizon=30000):
    source = taskset()
    adjudicable = observability.adjudicable_jobs_from_taskset(
        source, horizon
    )
    tasks = []
    for rank in range(10):
        name = f"task_{rank}"
        count = adjudicable[name]
        tasks.append(
            {
                "task_name": name,
                "priority_rank": rank,
                "is_top4": rank < 4,
                "is_bottom6": rank >= 4,
                "released_jobs": count,
                "adjudicable_jobs": count,
                "completed_jobs": count,
                "terminated_jobs": 0,
                "deadline_miss_jobs": 0,
                "unfinished_at_horizon_jobs": 0,
                "executed_core_ticks": count,
                "completed_response_time_count": count,
                "completed_response_time_sum_ms": count,
                "completed_response_time_max_ms": 1,
            }
        )
    mechanism = {name: 0 for name in MECHANISM_FIELDS}
    mechanism["observed_decision_ticks"] = horizon
    return source, {
        "trace_schema_version": 3,
        "observability_summary_contract_version": 2,
        "observability_summary_horizon_ms": horizon,
        "mechanism_summary": mechanism,
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
            "observed_energy_intervals": horizon,
        },
        "per_task_summary": tasks,
    }


class I5BObservabilityV2Tests(unittest.TestCase):
    def tearDown(self):
        analysis.configure_analysis_contract(1)

    def test_v2_recomputes_adjudicable_and_rejects_self_report_drift(self):
        source, document = summary_v2()
        report = observability.validate_schema3_summary(
            document,
            expected_horizon_ms=30000,
            initial_energy_j=0.5,
            capacity_j=1.0,
            processor_count=4,
            expected_task_ranks=observability.task_ranks_from_taskset(source),
            taskset_document=source,
            expected_contract_version=2,
        )
        self.assertEqual(
            report["observability_summary_contract_version"], 2
        )
        drift = copy.deepcopy(document)
        drift["per_task_summary"][0]["adjudicable_jobs"] -= 1
        with self.assertRaises(
            observability.ObservabilityValidationError
        ):
            observability.validate_schema3_summary(
                drift,
                expected_horizon_ms=30000,
                initial_energy_j=0.5,
                capacity_j=1.0,
                processor_count=4,
                taskset_document=source,
                expected_contract_version=2,
            )

    def test_deadline_equality_is_included_and_greater_is_excluded(self):
        source = {
            "taskset": [
                {
                    "name": "equal",
                    "iat": 100,
                    "deadline": 100,
                    "params": "arrival_offset=9900",
                },
                {
                    "name": "greater",
                    "iat": 100,
                    "deadline": 101,
                    "params": "arrival_offset=9900",
                },
            ]
        }
        counts = observability.adjudicable_jobs_from_taskset(
            source, 10000
        )
        self.assertEqual(counts, {"equal": 1, "greater": 0})

    def test_contract_v2_field_order_and_frozen_v1_bytes(self):
        contract = observability.CONTRACTS[2]
        self.assertEqual(
            tuple(item["name"] for item in contract["mechanism_summary_fields"]),
            MECHANISM_FIELDS,
        )
        task_fields = tuple(
            item["name"] for item in contract["per_task_summary_fields"]
        )
        self.assertEqual(task_fields[4:6], ("released_jobs", "adjudicable_jobs"))
        for name, expected in FROZEN_V1_SHA256.items():
            self.assertEqual(
                hashlib.sha256((B4_DIR / name).read_bytes()).hexdigest(),
                expected,
            )

    def test_minimum_100_is_accepted_and_99_is_rejected(self):
        source, document = summary_v2()
        for task in source["taskset"]:
            task.update(iat=300, deadline=300)
            task["params"] = "period=300,wcet=1,arrival_offset=0,workload=hash"
        for task in document["per_task_summary"]:
            for name in (
                "released_jobs", "adjudicable_jobs", "completed_jobs",
                "executed_core_ticks", "completed_response_time_count",
                "completed_response_time_sum_ms",
            ):
                task[name] = 100
        observability.validate_schema3_summary(
            document,
            expected_horizon_ms=30000,
            initial_energy_j=0.5,
            capacity_j=1.0,
            processor_count=4,
            taskset_document=source,
            expected_contract_version=2,
        )

        source["taskset"][0]["params"] = (
            "period=300,wcet=1,arrival_offset=1,workload=hash"
        )
        first = document["per_task_summary"][0]
        for name in (
            "released_jobs", "adjudicable_jobs", "completed_jobs",
            "executed_core_ticks", "completed_response_time_count",
            "completed_response_time_sum_ms",
        ):
            first[name] = 99
        with self.assertRaises(observability.ObservabilityValidationError):
            observability.validate_schema3_summary(
                document,
                expected_horizon_ms=30000,
                initial_energy_j=0.5,
                capacity_j=1.0,
                processor_count=4,
                taskset_document=source,
                expected_contract_version=2,
            )

    def test_v2_pass_and_v1_pass_are_explicitly_distinct(self):
        analysis.configure_analysis_contract(2)
        task = {
            name: 0 for name in analysis.TASK_METRIC_FIELDS
        }
        task.update(
            adjudicable_jobs=100,
            released_jobs=101,
            completed_jobs=100,
            unfinished_at_horizon_jobs=1,
        )
        self.assertTrue(analysis.task_pass(task))
        analysis.configure_analysis_contract(1)
        historical = {
            name: task[name] for name in analysis.TASK_METRIC_FIELDS
        }
        self.assertFalse(analysis.task_pass(historical))

    def test_v3_protocols_bind_v2_contracts_and_activation(self):
        for protocol in (
            manifest.PROTOCOL_V3,
            execution.PROTOCOL_V3,
            smoke.PROTOCOL_V3,
        ):
            self.assertEqual(
                protocol["observability_summary_contract_version"], 2
            )
            self.assertEqual(
                protocol["minimum_adjudicable_jobs_per_task"], 100
            )
            self.assertEqual(len(protocol["mechanism_fields"]), 13)
            self.assertEqual(
                protocol["jmr_denominator_contract"]["zero_denominator"],
                "NA",
            )
        case = manifest.build_case(
            "pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK",
            manifest.PROTOCOL_V3,
        )
        self.assertEqual(
            case["command_argv"][-5:],
            [
                "--b4-observability-summary",
                "--b4-summary-horizon",
                "30000",
                "--b4-observability-contract-version",
                "2",
            ],
        )

    def test_candidate_v3_binds_all_v3_manifest_bytes(self):
        candidate = json.loads(
            (B4_DIR / "b4_pe_freeze_candidate_v3.json").read_text()
        )
        self.assertEqual(candidate["freeze_status"], "candidate")
        self.assertIsNone(candidate["final_code_commit"])
        self.assertTrue(
            candidate["governance"]["not_final_until_rta_integration"]
        )
        for phase in ("pilot", "formal_main", "negative_control", "all"):
            rendered = manifest.render_manifest(
                phase, manifest.PROTOCOL_V3
            )
            expected = candidate["manifest_v3_identities"][phase]
            self.assertEqual(len(rendered.splitlines()), expected["case_count"])
            self.assertEqual(
                hashlib.sha256(rendered).hexdigest(), expected["sha256"]
            )


if __name__ == "__main__":
    unittest.main()
