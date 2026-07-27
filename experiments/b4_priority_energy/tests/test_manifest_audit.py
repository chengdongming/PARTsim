import sys
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import manifest_common as manifest


class ManifestAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pilot = list(manifest.iter_cases("pilot"))
        cls.all_records = list(manifest.iter_cases("all"))

    def test_pilot_audit_counts(self):
        summary = manifest.audit_records(self.pilot)
        self.assertEqual(summary["case_count"], 2400)
        self.assertEqual(summary["unique_taskset_count"], 60)
        self.assertEqual(summary["unique_source_count"], 240)
        self.assertEqual(summary["basic_unit_count"], 480)
        self.assertEqual(summary["complete_basic_unit_count"], 480)
        self.assertTrue(all(summary["protocol_sha_status"].values()))

    def test_all_audit_counts_and_reuse(self):
        summary = manifest.audit_records(self.all_records)
        self.assertEqual(summary["case_count"], 25800)
        self.assertEqual(summary["unique_taskset_count"], 560)
        self.assertEqual(summary["unique_source_count"], 2240)
        self.assertEqual(summary["formal_negative_taskset_reuse_count"], 300)
        self.assertEqual(summary["formal_negative_source_reuse_count"], 600)

    def test_audit_output_is_stable(self):
        first = manifest.compact_json(manifest.audit_records(self.pilot))
        second = manifest.compact_json(manifest.audit_records(self.pilot))
        self.assertEqual(first, second)

    def test_audit_reports_negative_diagnostics(self):
        records = list(self.pilot)
        records.pop(0)
        records.append(dict(records[0]))
        records[-1]["result_relpath"] = records[1]["result_relpath"]
        summary = manifest.audit_records(records)
        self.assertGreater(summary["missing_algorithm_count"], 0)
        self.assertGreater(summary["duplicate_count"], 0)
        self.assertGreater(summary["output_path_conflict_count"], 0)


if __name__ == "__main__":
    unittest.main()
