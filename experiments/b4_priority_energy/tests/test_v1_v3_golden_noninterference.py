import hashlib
import json
import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import execution_common as execution
import manifest_common as manifest


CASE_ID = (
    "case-3a8b02d0a0e0d8fefc8dd1617ac5bd86ed8bc75f464963789856c060b13f0e90"
)
TASKSET_PATH = (
    "artifacts/tasksets/"
    "ts-76ef159067fe346e01596b1443f4f14d7dc6e3c0689360ea71328623288da5c6.yml"
)
SOURCE_PATH = (
    "artifacts/sources/"
    "src-dc2baf44e8076f4b5e42482a53140678a70583cb402fe225347ffe1dba62b060.json"
)
SYSTEM_PATH = f"artifacts/configs/gpfp_asap_block/{CASE_ID}.yml"

GOLDEN = {
    1: {
        "manifest_sha256":
            "b3adfb138d72611c5a4013a523c1c38ab7d40edbda533239ba02b617be407497",
        "record_sha256":
            "751a9cab3172a22c3e1615dc2908df5cb93e7637a1156b43c05ad98cf63f754b",
        "argv_sha256":
            "2bee60cecbb661edf6a7bec10b0eb09ae3b20cc1238a68e9a18ca2386ebd2e6a",
        "result_path": f"results/pilot/{CASE_ID}.txt",
    },
    2: {
        "manifest_sha256":
            "79235b4413d3c76cee7dd8d33e5691357d02288ab2da9474949c44ff445af54f",
        "record_sha256":
            "5d850e7100f7fe74b0b10103a465d9fa9b558a5dfa1a59df89e6af52c70d43cd",
        "argv_sha256":
            "8deaf1170be6ab62913b3a6afc56157f0d0fd7cb764ac95b68dde024ffef74a4",
        "result_path": f"results/pilot/{CASE_ID}.json",
    },
    3: {
        "manifest_sha256":
            "a89e06795f66e4f455032cea6737a95b750bb60bfdc8eee60740a22116d8df8f",
        "record_sha256":
            "80d2941d686e8621fb37cb9d1b30ab45da63cc6d44c3cd5c6b8face3a015a806",
        "argv_sha256":
            "072b0989eaa54f9660b1dcda6d2fe02eb833197c38dc307a9e882de53d761540",
        "result_path": f"results/pilot/{CASE_ID}.json",
    },
}


class HistoricalGoldenNoninterferenceTests(unittest.TestCase):
    def test_v1_v3_manifest_record_paths_and_argv_are_byte_golden(self):
        protocols = {
            1: manifest.PROTOCOL_V1,
            2: manifest.PROTOCOL,
            3: manifest.PROTOCOL_V3,
        }
        for version, protocol in protocols.items():
            with self.subTest(version=version):
                expected = GOLDEN[version]
                rendered = manifest.render_manifest("all", protocol)
                record = next(manifest.iter_cases("pilot", protocol))
                self.assertEqual(
                    hashlib.sha256(rendered).hexdigest(),
                    expected["manifest_sha256"],
                )
                self.assertEqual(record["case_id"], CASE_ID)
                self.assertEqual(
                    hashlib.sha256(
                        manifest.compact_json(record).encode("utf-8")
                    ).hexdigest(),
                    expected["record_sha256"],
                )
                self.assertEqual(
                    hashlib.sha256(
                        manifest.compact_json(
                            record["command_argv"]
                        ).encode("utf-8")
                    ).hexdigest(),
                    expected["argv_sha256"],
                )
                self.assertEqual(
                    {
                        "taskset": record["taskset_artifact_relpath"],
                        "source": record["source_artifact_relpath"],
                        "system": record[
                            "system_config_artifact_relpath"
                        ],
                        "result": record["result_relpath"],
                    },
                    {
                        "taskset": TASKSET_PATH,
                        "source": SOURCE_PATH,
                        "system": SYSTEM_PATH,
                        "result": expected["result_path"],
                    },
                )
                self.assertNotIn(
                    manifest.SEMANTIC_HASH_PLACEHOLDER,
                    record["command_argv"],
                )

    def test_historical_protocol_candidate_and_provenance_goldens(self):
        expected_file_hashes = {
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
            "b4_pe_freeze_candidate_v1.json":
                "d5d2e6cfe7751f15227cb93ca66b17d11455cf5dccbcd768aebafb8623822732",
            "b4_pe_freeze_candidate_v2.json":
                "31bb158c5d1312850478331a7beb6c6c2da4d74f9639c23672dd8a976396e8ef",
            "b4_pe_freeze_candidate_v3.json":
                "c30c74c971cb82f01d243f733e5276b04ff4e862d317fe501a235c55070712cf",
        }
        for name, expected in expected_file_hashes.items():
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256((B4_DIR / name).read_bytes()).hexdigest(),
                    expected,
                )
        self.assertEqual(
            execution.SNAPSHOT_ROLES,
            ("simulator", "system", "taskset", "source"),
        )
        fingerprint_bytes = json.dumps(
            list(execution.FINGERPRINT_FIELDS),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(fingerprint_bytes).hexdigest(),
            "5d925d79c94f77009dd52b5c659e625df7d64efbee0b7e9e4946cc28d6e83ed7",
        )


if __name__ == "__main__":
    unittest.main()
