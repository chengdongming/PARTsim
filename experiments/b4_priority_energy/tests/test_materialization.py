import copy
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

import yaml


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import manifest_common as manifest
import materialization_common as materialization
import admission_common as admission


def _task(task_id, period, runtime, deadline, offset):
    return {
        "name": f"task_{task_id}",
        "iat": period,
        "runtime": runtime,
        "deadline": deadline,
        "params": (
            f"period={period},wcet={runtime},arrival_offset={offset},"
            "workload=hash"
        ),
        "code": [f"fixed({runtime}, hash)"],
    }


def fixed_base_taskset():
    # Deliberately place task_2 before task_1 with an equal period.  RM must
    # break the tie by task_id, not YAML load position.
    tasks = [
        _task(2, 50, 4, 40, 2),
        _task(7, 110, 7, 100, 7),
        _task(1, 50, 3, 35, 1),
        _task(9, 150, 8, 140, 9),
        _task(0, 40, 2, 28, 0),
        _task(5, 90, 6, 80, 5),
        _task(4, 80, 5, 70, 4),
        _task(8, 130, 7, 120, 8),
        _task(3, 70, 5, 60, 3),
        _task(6, 100, 6, 90, 6),
    ]
    return {"metadata": {"fixture": "fixed-ten-task-base"}, "taskset": tasks}


def _params(task):
    return materialization.parse_canonical_task_params(
        task["params"], require_factor=True
    )


def _calculation_view(document):
    result = {}
    for task in document["taskset"]:
        item = copy.deepcopy(task)
        params = materialization.parse_canonical_task_params(
            item["params"], require_factor=True
        )
        params.pop("task_energy_factor")
        item["params"] = params
        result[item["name"]] = item
    return result


class RhoTasksetDerivationTests(unittest.TestCase):
    def setUp(self):
        self.base = fixed_base_taskset()

    def test_rho_one_writes_explicit_factor_one_into_yaml(self):
        document, energy = materialization.derive_execution_taskset(
            self.base, "1"
        )
        rendered = materialization.canonical_yaml_bytes(document)
        self.assertIn(b"\ntaskset:\n  - name:", rendered)
        self.assertIn(b"\n    code:\n      - fixed(", rendered)
        loaded = yaml.safe_load(rendered)
        self.assertEqual(
            [_params(task)["task_energy_factor"] for task in loaded["taskset"]],
            ["1"] * 10,
        )
        self.assertEqual(energy["high_factor"], Fraction(1, 1))
        self.assertEqual(energy["low_factor"], Fraction(1, 1))

    def test_rho_two_uses_rm_task_id_tie_break_and_exact_normalization(self):
        document, energy = materialization.derive_execution_taskset(
            self.base, "2"
        )
        factors = {
            task["name"]: Fraction(_params(task)["task_energy_factor"])
            for task in document["taskset"]
        }
        expected_top = {"task_0", "task_1", "task_2", "task_3"}
        self.assertEqual(
            {name for name, value in factors.items()
             if value == Fraction(energy["high_factor_text"])},
            expected_top,
        )
        for name, value in factors.items():
            expected = (
                Fraction(energy["high_factor_text"])
                if name in expected_top
                else Fraction(energy["low_factor_text"])
            )
            self.assertEqual(value, expected)
        high = Fraction(energy["high_factor_text"])
        low = Fraction(energy["low_factor_text"])
        self.assertLessEqual(abs(high / low - 2), Fraction(1, 10**15))
        observed = high * energy["W_H_j"] + low * energy["W_L_j"]
        expected = energy["W_H_j"] + energy["W_L_j"]
        self.assertLessEqual(
            abs(observed - expected),
            Fraction(1, 10**15) * max(Fraction(1), abs(expected)),
        )

    def test_rho_changes_only_task_energy_factor(self):
        rho_one, _ = materialization.derive_execution_taskset(self.base, "1")
        rho_two, _ = materialization.derive_execution_taskset(self.base, "2")
        self.assertEqual(_calculation_view(rho_one), _calculation_view(rho_two))
        self.assertEqual(
            _calculation_view(rho_one),
            {
                task["name"]: {
                    **copy.deepcopy(task),
                    "params": materialization.parse_canonical_task_params(
                        task["params"], require_factor=False
                    ),
                }
                for task in self.base["taskset"]
            },
        )

    def test_invalid_or_noncanonical_factor_inputs_fail_closed(self):
        mutations = (
            "period=40,wcet=2,arrival_offset=0,workload=hash,"
            "task_energy_factor=1,task_energy_factor=2",
            "period=40,wcet=2,arrival_offset=0,workload=hash,"
            "task_energy_factor=nan",
            "period=40,wcet=2,arrival_offset=0,workload=hash,"
            "task_energy_factor=0",
            "wcet=2,period=40,arrival_offset=0,workload=hash",
        )
        for params in mutations:
            changed = copy.deepcopy(self.base)
            changed["taskset"][0]["params"] = params
            with self.subTest(params=params):
                with self.assertRaises(materialization.MaterializationError):
                    materialization.derive_execution_taskset(changed, "2")

    def test_release_counts_and_semantics_are_offset_aware(self):
        horizon = materialization.HORIZON_MS
        for task in self.base["taskset"]:
            offset = int(
                task["params"].split("arrival_offset=", 1)[1]
                .split(",", 1)[0]
            )
            expected = (
                0 if offset >= horizon
                else (horizon - 1 - offset) // task["iat"] + 1
            )
            self.assertEqual(
                materialization._release_count(task["iat"], offset),
                expected,
            )
        original = materialization.canonical_yaml_bytes(self.base)
        changed = copy.deepcopy(self.base)
        task = changed["taskset"][0]
        params = materialization.parse_canonical_task_params(
            task["params"], require_factor=False
        )
        params["arrival_offset"] = str(
            (int(params["arrival_offset"]) + 1) % task["iat"]
        )
        task["params"] = ",".join(
            f"{name}={params[name]}"
            for name in materialization.BASE_PARAM_KEYS
        )
        self.assertNotEqual(
            materialization.taskset_semantic_hash_bytes(original),
            materialization.taskset_semantic_hash_bytes(
                materialization.canonical_yaml_bytes(changed)
            ),
        )


class ManifestV4IdentityTests(unittest.TestCase):
    def test_rho_specific_paths_preserve_base_and_source_identity(self):
        one = manifest.build_case(
            "pilot", "0.3", 1, "0.85", "1", "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )
        two = manifest.build_case(
            "pilot", "0.3", 1, "0.85", "2", "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )
        self.assertEqual(one["taskset_id"], two["taskset_id"])
        self.assertEqual(one["source_id"], two["source_id"])
        self.assertNotEqual(one["case_id"], two["case_id"])
        self.assertNotEqual(one["result_relpath"], two["result_relpath"])
        self.assertEqual(
            one["base_taskset_artifact_relpath"],
            two["base_taskset_artifact_relpath"],
        )
        self.assertNotEqual(
            one["taskset_artifact_relpath"],
            two["taskset_artifact_relpath"],
        )
        self.assertIn("/rho-1.", one["taskset_artifact_relpath"])
        self.assertIn("/rho-2.", two["taskset_artifact_relpath"])

    def test_seeded_generator_is_deterministic_and_asynchronous(self):
        record = manifest.build_case(
            "pilot", "0.3", 1, "0.85", "1", "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )
        first_document, first_payload = (
            materialization.generate_base_taskset(record)
        )
        second_document, second_payload = (
            materialization.generate_base_taskset(record)
        )
        self.assertEqual(first_payload, second_payload)
        self.assertEqual(first_document, second_document)
        offsets = [
            int(materialization.parse_canonical_task_params(
                task["params"], require_factor=False
            )["arrival_offset"])
            for task in first_document["taskset"]
        ]
        self.assertTrue(any(offset != 0 for offset in offsets))
        self.assertTrue(all(
            0 <= offset < task["iat"]
            for offset, task in zip(offsets, first_document["taskset"])
        ))

    def test_v4_full_pilot_manifest_validates_without_identity_changes(self):
        records = list(manifest.iter_cases("pilot", manifest.PROTOCOL_V4))
        self.assertEqual(len(records), 2400)
        self.assertIs(manifest.validate_records(records), records)
        self.assertEqual(
            {record["taskset_id"] for record in records
             if record["replicate_index"] == 1
             and record["utilization"] == "0.3"},
            {
                manifest.build_case(
                    "pilot", "0.3", 1, "0.70", "1", "ASAP-BLOCK"
                )["taskset_id"]
            },
        )

    def test_v4_pilot_governance_rejects_partial_campaign(self):
        expected = {
            "formal_runs_authorized": False,
            "negative_control_runs_authorized": False,
            "paper_result_authorized": False,
            "pilot_runs_authorized": True,
        }
        self.assertEqual(manifest.PROTOCOL_V4["governance"], expected)
        self.assertEqual(execution.PROTOCOL_V4["governance"], expected)
        candidate = json.loads(
            (B4_DIR / "b4_pe_freeze_candidate_v4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {
                key: candidate["governance"][key]
                for key in expected
            },
            expected,
        )
        record = manifest.build_case(
            "pilot", "0.3", 1, "0.85", "1", "ASAP-BLOCK",
            manifest.PROTOCOL_V4,
        )
        with self.assertRaisesRegex(execution.SafetyError, "partial"):
            execution.execute_validated_cases(
                [record],
                "/does/not/matter",
                "/does/not/matter",
                "/does/not/matter",
            )


class MaterializationIntegrationTests(unittest.TestCase):
    @staticmethod
    def _paired_records():
        algorithms = manifest.IDENTITY.RESOLUTION["phase_algorithms"]["pilot"]
        records = [
            manifest.build_case(
                "pilot", "0.3", 1, "0.85", rho, algorithm,
                manifest.PROTOCOL_V4,
            )
            for rho in ("1", "2")
            for algorithm in algorithms
        ]
        return records

    @staticmethod
    def _write_admission(root, records, manifest_sha):
        cpu_system_relative = "artifacts/cpu-only-admission/system.yml"
        cpu_system_payload = admission.render_cpu_only_system()
        cpu_system_path = root / cpu_system_relative
        cpu_system_path.parent.mkdir(parents=True, exist_ok=True)
        cpu_system_path.write_bytes(cpu_system_payload)
        cpu_system_sha = materialization.bytes_sha256(cpu_system_payload)
        representatives = {}
        for record in records:
            representatives.setdefault(record["taskset_id"], record)
        entries = []
        for taskset_id, record in sorted(representatives.items()):
            _document, payload = materialization.generate_base_taskset(record)
            relative = record["base_taskset_artifact_relpath"]
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            entries.append(
                {
                    "taskset_pool": record["taskset_pool"],
                    "utilization": record["utilization"],
                    "replicate_index": record["replicate_index"],
                    "taskset_seed": record["taskset_seed"],
                    "taskset_id": taskset_id,
                    "base_taskset_path": relative,
                    "base_taskset_sha256":
                        materialization.bytes_sha256(payload),
                    "base_semantic_hash":
                        materialization.taskset_semantic_hash_bytes(payload),
                    "cpu_only_simulator_sha256": "1" * 64,
                    "cpu_only_system_config_sha256": cpu_system_sha,
                    "horizon_ms": materialization.HORIZON_MS,
                    "adjudicable_job_count": 1000,
                    "deadline_miss_count": 0,
                    "admission_status": "accepted",
                }
            )
        inventory = {
            "schema_version": 1,
            "protocol_name": "B4-PE-base-pool-admission-v1",
            "admission_protocol_sha256": materialization.file_sha256(
                materialization.ADMISSION_PROTOCOL_PATH
            ),
            "manifest_file_sha256": manifest_sha,
            "manifest_protocol_sha256": materialization.file_sha256(
                manifest.MANIFEST_PROTOCOL_V4_PATH
            ),
            "identity_protocol_sha256": materialization.file_sha256(
                manifest.IDENTITY_PROTOCOL_PATH
            ),
            "task_generator_sha256": materialization.file_sha256(
                materialization.TASK_GENERATOR_PATH
            ),
            "cpu_only_system_config_path": cpu_system_relative,
            "cpu_only_system_config_sha256": cpu_system_sha,
            "cpu_only_simulator_sha256": "1" * 64,
            "base_tasksets": entries,
        }
        relative = records[0]["base_pool_admission_inventory_relpath"]
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            materialization.canonical_json_bytes(inventory)
        )
        return inventory

    def _materialize(self, root):
        records = self._paired_records()
        self._write_admission(root, records, "a" * 64)
        return materialization.materialize_records(
            records,
            root,
            manifest_sha256="a" * 64,
        )

    def test_pairing_environment_inventory_and_determinism(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_root = Path(first_dir)
            second_root = Path(second_dir)
            first = self._materialize(first_root)
            repeated = self._materialize(first_root)
            second = self._materialize(second_root)
            self.assertEqual(first, repeated)
            self.assertEqual(
                materialization.canonical_json_bytes(first),
                materialization.canonical_json_bytes(second),
            )

            for section in (
                "base_tasksets", "execution_tasksets", "sources",
                "system_configs",
            ):
                first_by_path = {
                    entry["path"]: entry for entry in first[section]
                }
                second_by_path = {
                    entry["path"]: entry for entry in second[section]
                }
                self.assertEqual(set(first_by_path), set(second_by_path))
                for relative in first_by_path:
                    self.assertEqual(
                        (first_root / relative).read_bytes(),
                        (second_root / relative).read_bytes(),
                    )
                    self.assertEqual(
                        first_by_path[relative]["sha256"],
                        hashlib.sha256(
                            (first_root / relative).read_bytes()
                        ).hexdigest(),
                    )

            tasksets = first["execution_tasksets"]
            self.assertEqual(len(tasksets), 2)
            self.assertNotEqual(tasksets[0]["path"], tasksets[1]["path"])
            self.assertNotEqual(tasksets[0]["sha256"], tasksets[1]["sha256"])

            sources = first["sources"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(
                {case["source_artifact_sha256"] for case in first["cases"]},
                {sources[0]["sha256"]},
            )
            self.assertEqual(
                len({
                    case["offered_harvest_trace_sha256"]
                    for case in first["cases"]
                }),
                1,
            )
            for field in ("E0_j", "Emax_j", "alpha_w"):
                self.assertEqual(
                    len({case[field] for case in first["cases"]}), 1
                )

            algorithms = manifest.IDENTITY.RESOLUTION["phase_algorithms"]["pilot"]
            for rho in ("1", "2"):
                paired = [
                    case for case in first["cases"] if case["rho_E"] == rho
                ]
                self.assertEqual(
                    {case["algorithm"] for case in paired}, set(algorithms)
                )
                self.assertEqual(
                    len({case["execution_taskset_sha256"] for case in paired}),
                    1,
                )
                self.assertEqual(
                    len({case["source_artifact_sha256"] for case in paired}),
                    1,
                )

    def test_materialized_input_closes_executor_snapshot_and_semantic_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = self._paired_records()
            manifest_path = root / "manifest.identity"
            manifest_path.write_bytes(b"materialized-manifest-identity\n")
            manifest_sha = hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
            self._write_admission(root, records, manifest_sha)
            inventory = materialization.materialize_records(
                records,
                root,
                manifest_sha256=manifest_sha,
            )
            simulator = root / "fake_simulator.py"
            shutil.copyfile(B4_DIR / "tests" / "fake_simulator.py", simulator)
            simulator.chmod(0o755)
            context = execution.build_context(
                manifest_path,
                root,
                simulator,
                execution.EXECUTION_PROTOCOL_V4_SHA256,
            )
            try:
                record = records[0]
                provenance = execution.build_provenance(record, context)
                entry = next(
                    item for item in inventory["execution_tasksets"]
                    if item["path"] == record["taskset_artifact_relpath"]
                )
                self.assertEqual(
                    provenance["taskset_artifact_sha256"], entry["sha256"]
                )
                self.assertEqual(
                    provenance["taskset_semantic_hash"],
                    entry["semantic_hash"],
                )
                context["active_provenance"] = provenance
                attempt_fd = os.open(
                    root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    argv = execution.build_execution_argv(
                        record,
                        context,
                        1,
                        attempt_fd,
                        "trace.json",
                        {
                            "simulator": {"proc_fd_path": "/proc/self/fd/10"},
                            "system": {"proc_fd_path": "/proc/self/fd/11"},
                            "taskset": {"proc_fd_path": "/proc/self/fd/12"},
                            "source": {"proc_fd_path": "/proc/self/fd/13"},
                        },
                    )
                finally:
                    os.close(attempt_fd)
                semantic_index = argv.index("--taskset-semantic-hash")
                self.assertEqual(
                    argv[semantic_index + 1], entry["semantic_hash"]
                )
                self.assertNotIn(
                    materialization.SEMANTIC_HASH_PLACEHOLDER, argv
                )
                snapshot = Path(provenance["taskset_executed_snapshot_path"])
                taskset = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
                self.assertEqual(
                    [_params(task)["task_energy_factor"]
                     for task in taskset["taskset"]],
                    ["1"] * 10,
                )
                self.assertEqual(
                    provenance["inventory_snapshot_sha256"],
                    provenance["materialization_inventory_sha256"],
                )
                original_inventory = root / record[
                    "materialization_inventory_relpath"
                ]
                original_inventory.write_bytes(b"replaced after snapshot\n")
                opened = execution._open_execution_snapshots(
                    context, provenance
                )
                try:
                    identity = (
                        execution._open_and_validate_v4_inventory_snapshot(
                            record, context, provenance, opened["taskset"]
                        )
                    )
                    try:
                        self.assertNotIn(
                            identity["fd"],
                            {
                                item["fd"] for item in opened.values()
                            },
                        )
                    finally:
                        os.close(identity["fd"])
                finally:
                    execution._close_execution_snapshots(opened)
            finally:
                execution.close_context(context)

    def test_inventory_snapshot_damage_fails_before_popen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = self._paired_records()
            manifest_path = root / "manifest.identity"
            manifest_path.write_bytes(b"materialized-manifest-identity\n")
            manifest_sha = hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
            self._write_admission(root, records, manifest_sha)
            materialization.materialize_records(
                records, root, manifest_sha256=manifest_sha
            )
            simulator = root / "fake_simulator.py"
            shutil.copyfile(B4_DIR / "tests" / "fake_simulator.py", simulator)
            simulator.chmod(0o755)
            context = execution.build_context(
                manifest_path,
                root,
                simulator,
                execution.EXECUTION_PROTOCOL_V4_SHA256,
            )
            try:
                record = records[0]
                provenance = execution.build_provenance(record, context)
                snapshot = root / provenance[
                    "inventory_snapshot_relpath"
                ]
                snapshot.chmod(0o600)
                snapshot.write_bytes(b"damaged\n")
                opened = execution._open_execution_snapshots(
                    context, provenance
                )
                try:
                    with self.assertRaises(execution.InputIntegrityError):
                        execution._open_and_validate_v4_inventory_snapshot(
                            record, context, provenance, opened["taskset"]
                        )
                finally:
                    execution._close_execution_snapshots(opened)
            finally:
                execution.close_context(context)

    def test_retry_revalidates_the_same_inventory_snapshot_closure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = self._paired_records()
            record = records[0]
            manifest_path = root / "manifest.identity"
            manifest_path.write_bytes(b"materialized-manifest-identity\n")
            manifest_sha = hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
            self._write_admission(root, records, manifest_sha)
            materialization.materialize_records(
                records, root, manifest_sha256=manifest_sha
            )
            simulator = root / "simulator"
            simulator.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            simulator.chmod(0o755)
            context = execution.build_context(
                manifest_path,
                root,
                simulator,
                execution.EXECUTION_PROTOCOL_V4_SHA256,
            )

            class Process:
                def __init__(self, argv, timeout):
                    self.argv = argv
                    self.timeout = timeout
                    self.returncode = None
                    self.pid = 12345

                def wait(self, timeout):
                    if self.timeout:
                        raise execution.subprocess.TimeoutExpired(
                            self.argv, timeout
                        )
                    trace = Path(
                        self.argv[self.argv.index("-t") + 1]
                    )
                    trace.write_bytes(b"{}\n")
                    self.returncode = 0
                    return 0

                def poll(self):
                    return self.returncode

            processes = iter((True, False))

            def popen(argv, **_kwargs):
                return Process(argv, next(processes))

            def terminate(process, _grace):
                process.returncode = -15

            try:
                provenance = execution.build_provenance(record, context)
                context["active_provenance"] = provenance
                state = execution.new_state(provenance)
                real_validate = (
                    execution._open_and_validate_v4_inventory_snapshot
                )
                with mock.patch.object(
                    execution,
                    "_open_and_validate_v4_inventory_snapshot",
                    wraps=real_validate,
                ) as validate, mock.patch.object(
                    execution.subprocess, "Popen", side_effect=popen
                ), mock.patch.object(
                    execution,
                    "_terminate_process_group",
                    side_effect=terminate,
                ):
                    self.assertEqual(
                        execution.run_attempt(record, context, state, 1),
                        "timed_out",
                    )
                    (root / record[
                        "materialization_inventory_relpath"
                    ]).write_bytes(b"replaced original inventory\n")
                    self.assertEqual(
                        execution.run_attempt(record, context, state, 2),
                        "succeeded",
                    )
                self.assertEqual(validate.call_count, 2)
                self.assertEqual(
                    {
                        call.args[2]["inventory_snapshot_sha256"]
                        for call in validate.call_args_list
                    },
                    {provenance["inventory_snapshot_sha256"]},
                )
            finally:
                execution.close_context(context)

    def test_conflicting_existing_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = self._paired_records()
            self._write_admission(root, records, "c" * 64)
            first = records[0]
            conflict = root / first["taskset_artifact_relpath"]
            conflict.parent.mkdir(parents=True, exist_ok=True)
            conflict.write_text("conflicting\n", encoding="utf-8")
            with self.assertRaises(materialization.MaterializationError):
                materialization.materialize_records(
                    records, root, manifest_sha256="c" * 64
                )

    def test_materializer_only_consumes_admitted_bases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            records = self._paired_records()
            self._write_admission(root, records, "d" * 64)
            with mock.patch.object(
                materialization,
                "generate_base_taskset",
                side_effect=AssertionError("materializer generated a base"),
            ):
                materialization.materialize_records(
                    records, root, manifest_sha256="d" * 64
                )

    def test_rejected_or_tampered_admission_fails_closed(self):
        for mutation in ("rejected", "tampered"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                records = self._paired_records()
                inventory = self._write_admission(
                    root, records, "e" * 64
                )
                entry = inventory["base_tasksets"][0]
                if mutation == "rejected":
                    entry["admission_status"] = "rejected"
                    entry["deadline_miss_count"] = 1
                    relative = records[0][
                        "base_pool_admission_inventory_relpath"
                    ]
                    (root / relative).write_bytes(
                        materialization.canonical_json_bytes(inventory)
                    )
                else:
                    base = root / entry["base_taskset_path"]
                    base.write_bytes(base.read_bytes() + b"# drift\n")
                with self.assertRaises(
                    materialization.MaterializationError
                ):
                    materialization.materialize_records(
                        records, root, manifest_sha256="e" * 64
                    )

    def test_inventory_contains_no_runtime_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory = self._materialize(Path(temp_dir))
            encoded = materialization.canonical_json_bytes(inventory)
            lowered = encoded.lower()
            for forbidden in (
                b"timestamp", b"generated_at", b"pid",
                str(Path(temp_dir)).encode("utf-8").lower(),
            ):
                self.assertNotIn(forbidden, lowered)
            json.loads(encoded)
            for value in materialization.walk_inventory_numbers(inventory):
                self.assertTrue(math.isfinite(value))


if __name__ == "__main__":
    unittest.main()
