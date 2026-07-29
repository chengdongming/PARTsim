import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import admission_common as admission
import execution_common as execution
import execute_manifest_v4 as executor_cli
import manifest_common as manifest
import materialization_common as materialization


EXPECTED_GOVERNANCE = {
    "formal_runs_authorized": False,
    "negative_control_runs_authorized": False,
    "paper_result_authorized": False,
    "pilot_runs_authorized": True,
}


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


class V4PilotAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.manifest_path = cls.root / "pilot_v4.jsonl"
        cls.payload = manifest.render_manifest("pilot", manifest.PROTOCOL_V4)
        cls.manifest_path.write_bytes(cls.payload)
        cls.records = manifest.validate_manifest(cls.manifest_path)
        cls.authorization = execution.load_pilot_authorization_v4()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def _write_variant(self, name, payload):
        root = self.root / name
        root.mkdir()
        path = root / "pilot_v4.jsonl"
        path.write_bytes(payload)
        return path

    def assert_preflight_rejected(self, name, payload):
        path = self._write_variant(name, payload)
        with self.assertRaises(execution.ExecutionError):
            execution.preflight_authorized_v4_pilot_manifest(path)

    def test_all_protocol_governance_authorizes_only_pilot(self):
        documents = (
            manifest.load_candidate_v4(),
            manifest.PROTOCOL_V4,
            admission.load_protocol(),
            materialization.load_materialization_protocol(),
            execution.load_pilot_authorization_v4(),
            json.loads(
                execution.EXECUTION_PROTOCOL_V4_PATH.read_text(
                    encoding="utf-8"
                )
            ),
        )
        for document in documents:
            with self.subTest(document=document.get("protocol_name")):
                self.assertEqual(
                    {
                        field: document["governance"][field]
                        for field in EXPECTED_GOVERNANCE
                    },
                    EXPECTED_GOVERNANCE,
                )

    def test_candidate_scientific_and_final_identities_are_unchanged(self):
        candidate = manifest.load_candidate_v4()
        self.assertEqual(
            candidate["candidate_code_commit"],
            "681409e35012d2bc883045e4d10a048b36a6483f",
        )
        self.assertEqual(
            candidate["candidate_code_tree"],
            "266ecfdca0c1bc194e2ce77a295254893b9737ca",
        )
        for field in (
            "final_code_commit",
            "final_git_tag",
            "formal_runtime_binary_path",
            "formal_runtime_binary_sha256",
        ):
            self.assertIsNone(candidate[field])
        history = candidate["governance_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["transition_type"], "pilot_authorization")
        self.assertFalse(any(
            history[0][field]
            for field in (
                "algorithm_changes",
                "parameter_changes",
                "task_generation_changes",
                "scheduler_changes",
                "runtime_identity_changes",
                "rta_changes",
                "formal_authorized",
                "negative_control_authorized",
                "paper_result_authorized",
            )
        ))

    def test_manifest_is_exact_canonical_and_collision_free(self):
        self.assertEqual(len(self.records), 2400)
        self.assertEqual(
            _sha256_bytes(self.payload),
            self.authorization["pilot_manifest_sha256"],
        )
        self.assertEqual(
            self.payload,
            manifest.render_manifest("pilot", manifest.PROTOCOL_V4),
        )
        case_ids = set()
        canonical_records = set()
        seed_owners = {}
        taskset_owners = {}
        source_owners = {}
        for record, raw_line in zip(self.records, self.payload.splitlines()):
            self.assertEqual(
                raw_line,
                manifest.compact_json(record).encode("utf-8"),
            )
            self.assertEqual(record["schema_version"], 4)
            self.assertEqual(record["phase"], "pilot")
            self.assertEqual(
                record["candidate_v4_sha256"],
                self.authorization["candidate_sha256"],
            )
            self.assertEqual(
                record["manifest_protocol_sha256"],
                self.authorization["manifest_protocol_sha256"],
            )
            self.assertNotIn(record["case_id"], case_ids)
            case_ids.add(record["case_id"])
            canonical = manifest.compact_json(record)
            self.assertNotIn(canonical, canonical_records)
            canonical_records.add(canonical)
            task_semantic = (
                record["taskset_pool"],
                record["utilization"],
                record["replicate_index"],
            )
            source_semantic = (record["taskset_id"], record["lambda_E"])
            self.assertIn(
                seed_owners.setdefault(record["taskset_seed"], task_semantic),
                (task_semantic,),
            )
            self.assertIn(
                taskset_owners.setdefault(record["taskset_id"], task_semantic),
                (task_semantic,),
            )
            self.assertIn(
                source_owners.setdefault(record["source_id"], source_semantic),
                (source_semantic,),
            )
            for key, value in record.items():
                if key.endswith("relpath"):
                    self.assertFalse(PurePosixPath(value).is_absolute())
            strings = [
                value
                for value in record.values()
                if isinstance(value, str)
            ] + record["command_argv"]
            self.assertFalse(any(value.startswith("/") for value in strings))
            serialized = raw_line.lower()
            for forbidden in (
                b'"timestamp"',
                b'"generated_at"',
                b'"pid"',
                b'"execution_order"',
            ):
                self.assertNotIn(forbidden, serialized)

    def test_pilot_matrix_and_algorithm_order_are_exact(self):
        matrix = manifest.PROTOCOL_V4["phase_matrix"]["pilot"]
        self.assertEqual(matrix["utilization"], ["0.3", "0.4", "0.5"])
        self.assertEqual(matrix["lambda_E"], ["0.70", "0.85", "1.00", "1.15"])
        self.assertEqual(matrix["rho_E"], ["1", "2"])
        self.assertEqual(matrix["replicate_count"], 20)
        self.assertEqual(
            manifest.IDENTITY.RESOLUTION["phase_algorithms"]["pilot"],
            [
                "ASAP-BLOCK",
                "ASAP-NONBLOCK",
                "ASAP-SYNC",
                "ALAP-BLOCK",
                "ST-BLOCK",
            ],
        )
        self.assertEqual(
            {
                (record["M"], record["task_count"], record["horizon_ms"])
                for record in self.records
            },
            {(4, 10, 30000)},
        )

    def test_exact_manifest_static_preflight_precedes_subprocess(self):
        with mock.patch.object(execution.subprocess, "Popen") as popen:
            accepted = execution.preflight_authorized_v4_pilot_manifest(
                self.manifest_path
            )
        self.assertEqual(accepted, self.records)
        popen.assert_not_called()

    def test_v4_cli_preflight_does_not_start_subprocess(self):
        stdout = io.StringIO()
        with mock.patch.object(
            execution.subprocess, "Popen"
        ) as popen, mock.patch("sys.stdout", stdout):
            status = executor_cli.main(
                [
                    "--manifest",
                    str(self.manifest_path),
                    "--preflight-only",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["preflight_status"],
            "accepted",
        )
        popen.assert_not_called()

    def test_tampered_manifest_is_rejected(self):
        changed = dict(self.records[0])
        changed["candidate_v4_sha256"] = "0" * 64
        payload = (
            manifest.compact_json(changed).encode("utf-8")
            + b"\n"
            + b"\n".join(self.payload.splitlines()[1:])
            + b"\n"
        )
        self.assert_preflight_rejected("tampered", payload)

    def test_truncated_manifest_is_rejected(self):
        self.assert_preflight_rejected(
            "truncated",
            b"\n".join(self.payload.splitlines()[:-1]) + b"\n",
        )

    def test_appended_manifest_is_rejected(self):
        self.assert_preflight_rejected(
            "appended",
            self.payload + self.payload.splitlines(keepends=True)[0],
        )

    def test_reordered_manifest_is_rejected(self):
        lines = self.payload.splitlines(keepends=True)
        lines[0], lines[1] = lines[1], lines[0]
        self.assert_preflight_rejected("reordered", b"".join(lines))

    def test_wrong_protocol_sha_is_rejected(self):
        changed = dict(self.records[0])
        changed["manifest_protocol_sha256"] = "0" * 64
        lines = self.payload.splitlines(keepends=True)
        lines[0] = manifest.compact_json(changed).encode("utf-8") + b"\n"
        self.assert_preflight_rejected("wrong-protocol", b"".join(lines))

    def test_wrong_authorization_sha_is_rejected(self):
        protocol = json.loads(
            execution.EXECUTION_PROTOCOL_V4_PATH.read_text(encoding="utf-8")
        )
        protocol["pilot_authorization_sha256"] = "0" * 64
        path = self.root / "wrong_execution_protocol_v4.json"
        path.write_text(
            json.dumps(protocol, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            execution.ExecutionError,
            "authorization identity mismatch",
        ):
            execution.load_execution_protocol(path)

    def test_partial_formal_negative_and_paper_paths_are_rejected(self):
        partial = self.records[:1]
        with self.assertRaisesRegex(execution.SafetyError, "partial"):
            execution.execute_validated_cases(
                partial,
                self.manifest_path,
                "/invalid",
                "/invalid",
            )
        for phase in ("formal_main", "negative_control", "paper_result"):
            with self.subTest(phase=phase):
                changed = dict(self.records[0])
                changed["phase"] = phase
                payload = manifest.compact_json(changed).encode("utf-8") + b"\n"
                self.assert_preflight_rejected(f"phase-{phase}", payload)

    def test_authorization_dependency_graph_is_acyclic(self):
        authorization = self.authorization
        execution_protocol = json.loads(
            execution.EXECUTION_PROTOCOL_V4_PATH.read_text(encoding="utf-8")
        )
        candidate = json.loads(
            manifest.CANDIDATE_V4_PATH.read_text(encoding="utf-8")
        )
        manifest_protocol = json.loads(
            manifest.MANIFEST_PROTOCOL_V4_PATH.read_text(encoding="utf-8")
        )
        authorization_raw = execution.PILOT_AUTHORIZATION_V4_PATH.read_text(
            encoding="utf-8"
        )
        self.assertNotIn("pilot_manifest_sha256", candidate)
        self.assertNotIn("pilot_authorization_ref", candidate)
        self.assertNotIn("pilot_authorization_sha256", candidate)
        self.assertNotIn("pilot_authorization_ref", manifest_protocol)
        self.assertNotIn("pilot_authorization_sha256", manifest_protocol)
        self.assertNotIn("execution_protocol", authorization_raw)
        self.assertEqual(
            execution_protocol["pilot_authorization_sha256"],
            execution.file_sha256(execution.PILOT_AUTHORIZATION_V4_PATH),
        )
        graph = {
            "candidate": {"manifest_protocol", "pilot_manifest", "authorization"},
            "manifest_protocol": {
                "admission",
                "materialization",
                "pilot_manifest",
                "authorization",
            },
            "admission": {"materialization", "authorization"},
            "materialization": {"authorization"},
            "pilot_manifest": {"authorization"},
            "runtime_closure": {"authorization"},
            "authorization": {"execution_protocol"},
            "execution_protocol": set(),
        }
        visiting = set()
        visited = set()

        def visit(node):
            self.assertNotIn(node, visiting, "authorization dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for child in graph[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        self.assertEqual(set(graph), visited)
        self.assertEqual(
            authorization["pilot_manifest_filename"],
            "pilot_v4.jsonl",
        )

    def test_runtime_closure_is_bound_without_local_reverification_claim(self):
        closure = self.authorization["runtime_closure"]
        self.assertEqual(
            closure["binding_status"],
            "independently_verified_and_bound",
        )
        self.assertNotIn(
            "reverified_locally",
            json.dumps(self.authorization, sort_keys=True),
        )
        candidate = manifest.load_candidate_v4()["runtime_closure"]
        self.assertEqual(
            {
                name: entry["sha256"]
                for name, entry in closure["artifacts"].items()
            },
            {
                name: entry["sha256"]
                for name, entry in candidate["artifacts"].items()
            },
        )
        self.assertEqual(
            {
                name: entry["sha256"]
                for name, entry in closure["evidence"].items()
            },
            {
                name: entry["sha256"]
                for name, entry in candidate["evidence"].items()
            },
        )


if __name__ == "__main__":
    unittest.main()
