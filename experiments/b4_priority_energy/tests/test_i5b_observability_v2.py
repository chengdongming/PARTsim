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

SCIENCE_CODE_COMMIT = "87fae1924591fa2c0cabd292c03df043d5eea9fd"
PRE_PILOT_CANDIDATE_V3_SHA256 = (
    "708e3b90e294e560604e34e7052a3314a4fd7580b86295ebcd7e0182fada21cd"
)
FROZEN_GOVERNANCE_INPUT_SHA256 = {
    "B4_PE_FREEZE_CANDIDATE_v1.md":
        "bee0916c5b3b73084dc5d90a4353029648d1265339c6f084b0c924e4990d44f7",
    "B4_PE_FREEZE_CANDIDATE_v2.md":
        "2225a3888fe0fa85ed63639f1c7ba90bd02ba977e0245fc3397f1d69b9a0e7ff",
    "b4_pe_freeze_candidate_v1.json":
        "d5d2e6cfe7751f15227cb93ca66b17d11455cf5dccbcd768aebafb8623822732",
    "b4_pe_freeze_candidate_v2.json":
        "31bb158c5d1312850478331a7beb6c6c2da4d74f9639c23672dd8a976396e8ef",
    "manifest_protocol_v1.json":
        "e00a1fe5ccc4713a9b6b211dde8d6682919d0f599b16424deaf06661c17e148f",
    "manifest_protocol_v2.json":
        "4d1ead28d2b957ef0b8764f7148f2aab7643893f4134f8e56234bc913058ce90",
    "manifest_protocol_v3.json":
        "c51e774e74ad3ce9bb4d39bacfccb5a7c64e71750c6a0f12432c4ab70070603f",
    "execution_protocol_v1.json":
        "74fd9ed742ad41dbedb66a5e7de2bbc796e746ae2efb207d2d456deed10cdd34",
    "execution_protocol_v2.json":
        "632b737b1c7cff9dd70eb7c091561be5ac7e5902333b4006c0b40faa5c9f3cfb",
    "execution_protocol_v3.json":
        "b76a44ac48c1721e4a0b2042a53d787c22b78a0ec017ea171d92534fd1d107ec",
    "integration_smoke_protocol_v1.json":
        "4b7e47cd0e89e31540cc30317f91e54a64d45efe63fca05629ee16e5d1aded85",
    "integration_smoke_protocol_v2.json":
        "c1777b704048d9ae566c9ccc3d1e148393d030ec7dd2e002255dd7c9a8344efd",
    "integration_smoke_protocol_v3.json":
        "5517b35afab8c65ac1ab045b047f8032169abf2c3efa7c60a21de5d4d311d9fb",
    "observability_summary_contract_v1.json":
        "1817fdbf795f878769a32cf3f1e4ba09dd2ce589137e1d2e487a8df866b7c7f6",
    "observability_summary_contract_v2.json":
        "4e982f5a58a26507c9ab1b1b8d0b732e651d4657f10cf16744d3278d11186efe",
    "analysis_contract_v1.json":
        "15945810a1f5c9792056c88f2ed5e733b75cbdea6635c41c0cdda4163ba67f1f",
    "analysis_contract_v2.json":
        "25d0cfff0fba81979d15b5b70df842fc2e84f969574fa4cd73fc7ad2527c9318",
    "statistics_contract_v1.json":
        "2646f0e83f164fec7dccbe151b19e95b2efb13d223c673f6962c03d88803ca24",
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
        self.assertEqual(candidate["candidate_code_commit"], SCIENCE_CODE_COMMIT)
        self.assertEqual(candidate["freeze_status"], "candidate")
        for field in (
            "final_code_commit",
            "final_git_tag",
            "formal_runtime_binary_path",
            "formal_runtime_binary_sha256",
        ):
            self.assertIsNone(candidate[field])
        self.assertEqual(
            candidate["governance"],
            {
                "formal_runs_authorized": False,
                "i5d_statistics_authorized": True,
                "negative_control_runs_authorized": False,
                "not_final_until_independent_review": True,
                "not_final_until_rta_integration": True,
                "pilot_runs_authorized": True,
                "silent_changes_forbidden": True,
            },
        )
        history = candidate["governance_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0]["pre_pilot_candidate_v3_sha256"],
            PRE_PILOT_CANDIDATE_V3_SHA256,
        )
        self.assertEqual(history[0]["transition_type"], "pilot_authorization")
        self.assertEqual(history[0]["science_code_commit"], SCIENCE_CODE_COMMIT)
        for field in (
            "algorithm_changes",
            "parameter_changes",
            "protocol_changes",
            "statistics_contract_changes",
            "task_generation_changes",
        ):
            self.assertFalse(history[0][field])

        runtime_sha = candidate["pilot_runtime_binary_sha256"]
        self.assertRegex(runtime_sha, r"^[0-9a-f]{64}$")
        self.assertEqual(candidate["pilot_runtime_binary_path"], "pilot-runtime/bin/rtsim")
        self.assertNotIn("/tmp", candidate["pilot_runtime_binary_path"])
        self.assertGreater(candidate["pilot_runtime_binary_size_bytes"], 0)
        self.assertIn("ELF64", candidate["pilot_runtime_binary_artifact_type"])

        dependencies = candidate["pilot_runtime_dependencies"]
        self.assertEqual(
            [item["logical_path"] for item in dependencies],
            sorted(item["logical_path"] for item in dependencies),
        )
        for item in dependencies:
            self.assertEqual(
                set(item), {"logical_path", "role", "sha256", "size_bytes"}
            )
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(item["size_bytes"], 0)
            self.assertNotIn("/tmp", item["logical_path"])
        dependency_manifest = (
            json.dumps(
                dependencies,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(dependency_manifest).hexdigest(),
            candidate["pilot_runtime_dependency_manifest_sha256"],
        )

        build = candidate["pilot_release_build_identity"]
        self.assertEqual(build["science_code_commit"], SCIENCE_CODE_COMMIT)
        self.assertEqual(build["build_script_path"], "deployment/autodl/build_simulator.sh")
        self.assertEqual(
            build["build_script_sha256"],
            hashlib.sha256(
                (B4_DIR.parents[1] / build["build_script_path"]).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(build["cmake_configuration"]["build_type"], "Release")
        self.assertIs(build["cmake_configuration"]["build_testing"], False)
        self.assertIn("-DBUILD_TESTING=OFF", build["configure_argv"])
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", build["configure_argv"])
        for field in (
            "build_argv",
            "build_entrypoint_argv",
            "cmake_version",
            "compiler",
            "configure_argv",
            "key_environment",
            "numpy_version",
            "python_version",
        ):
            self.assertTrue(build[field])

        implementations = candidate["pilot_python_implementation_identities"]
        self.assertEqual(
            set(implementations),
            {
                "i5c_extractor",
                "i5d_statistics",
                "manifest_executor",
                "manifest_generator",
                "task_generator",
            },
        )
        for identity in implementations.values():
            path = identity.get("path", identity.get("entrypoint_path"))
            sha = identity.get("sha256", identity.get("entrypoint_sha256"))
            self.assertEqual(
                hashlib.sha256((B4_DIR.parents[1] / path).read_bytes()).hexdigest(),
                sha,
            )
            for item in identity.get("implementation_files", []):
                self.assertEqual(
                    hashlib.sha256(
                        (B4_DIR.parents[1] / item["path"]).read_bytes()
                    ).hexdigest(),
                    item["sha256"],
                )
        self.assertEqual(
            implementations["i5c_extractor"]["implementation_sha256"],
            "9b44bb7236ef03c5b6c65ed5a225f8507a300ce3a929acc6242ee6f17f2525e5",
        )
        self.assertEqual(
            implementations["i5d_statistics"]["implementation_sha256"],
            "7ed9e9a852252bb4228598378ce92c02879e2ca4f9161102c81117547d89a10f",
        )
        for name in ("i5c_extractor", "i5d_statistics"):
            digest = hashlib.sha256()
            for item in implementations[name]["implementation_files"]:
                relative = item["path"].encode("utf-8")
                material = (B4_DIR.parents[1] / item["path"]).read_bytes()
                digest.update(len(relative).to_bytes(8, "big"))
                digest.update(relative)
                digest.update(len(material).to_bytes(8, "big"))
                digest.update(material)
            self.assertEqual(
                digest.hexdigest(),
                implementations[name]["implementation_sha256"],
            )
        template = candidate["pilot_system_template_identity"]
        self.assertEqual(
            hashlib.sha256(
                (B4_DIR.parents[1] / template["path"]).read_bytes()
            ).hexdigest(),
            template["sha256"],
        )
        self.assertEqual(
            candidate["pilot_statistics_contract_sha256"],
            hashlib.sha256(
                (B4_DIR.parents[1] / candidate["pilot_statistics_contract_path"]).read_bytes()
            ).hexdigest(),
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

    def test_historical_candidates_protocols_and_contracts_are_byte_identical(self):
        for name, expected in FROZEN_GOVERNANCE_INPUT_SHA256.items():
            self.assertEqual(
                hashlib.sha256((B4_DIR / name).read_bytes()).hexdigest(),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
