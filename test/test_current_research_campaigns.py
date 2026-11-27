"""Small deterministic tests of the current entrypoints, not formal results."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.same_slot import campaign, ab_rta
from scripts import run_scheduler_comparison


class CurrentEntryTests(unittest.TestCase):
    def test_nine_scheduler_entry_fixes_energy_timing(self):
        base = ["--output", "runs/test", "--seed", "7"]
        args = run_scheduler_comparison.current_args(base)
        parsed = run_scheduler_comparison.existing.make_parser().parse_args(args)
        self.assertEqual(parsed.energy_timing, "same-slot")
        self.assertEqual(parsed.initial_energy_rule, "zero")
        for extra in (["--energy-timing", "debit-before-harvest"],
                      ["--experiment-version", "a-implicit-v1"],
                      ["--uc09-supplement"]):
            with self.assertRaises(ValueError):
                run_scheduler_comparison.current_args(base + extra)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.old = campaign.frozen_data().make_case(
            "small", [(1, 3, 3, 2), (1, 4, 4, 1)],
            2, [4*i for i in range(5)], release_floor=0)
        self.case = campaign.convert(self.old)
        self.methods = ["analytic", "translated_seq"]

    def test_identity_and_conversion(self):
        self.assertEqual(self.old["model"]["harvest_order"], "DEBIT_BEFORE_HARVEST")
        self.assertEqual(self.case["model"]["harvest_order"], "HARVEST_BEFORE_DEBIT")
        self.assertEqual(self.case["model"]["release_floor_stage"], "PRE_HARVEST")
        self.assertNotEqual(self.old["case_id"], self.case["case_id"])
        self.assertNotEqual(self.old["input_sha256"], self.case["input_sha256"])
        self.assertEqual(self.case["source_case_id"], self.old["case_id"])
        with self.assertRaises(ValueError):
            campaign.convert(self.case)

    def test_solver_calls_preserve_existing_math(self):
        for method in ("analytic", "flow"):
            new = campaign.evaluate(self.case, method)
            old = ab_rta.analyze_case(self.case, bound=method)
            self.assertEqual(new["status"], old["status"])
            self.assertEqual(new["result"]["certificate"], old["certificate"])
            self.assertEqual(new["result"]["verification"], old["verification"])
        translated = campaign.evaluate(self.case, "translated_seq")
        original = ab_rta.analyze_case(self.case, translated_seq=True)["translated_seq"]["result"]
        self.assertEqual(translated["result"], original)
        self.assertEqual(translated["status"], "CERTIFIED")

    def test_subprocess_timeout_and_csv_pairing(self):
        a = campaign.execute(self.case, "analytic", 10)
        self.assertEqual(a["status"], "CERTIFIED")
        with patch("experiments.same_slot.campaign.subprocess.run",
                   side_effect=campaign.subprocess.TimeoutExpired("worker", 0.01)):
            b = campaign.execute(self.case, "translated_seq", .01)
        self.assertEqual(b["status"], "TIMEOUT")
        rows = {(self.case["case_id"], r["method"]): r for r in (a,b)}
        summary, paired = campaign.summarize([self.case], self.methods, rows)
        self.assertEqual(summary[-1]["timeout"], 1)
        self.assertEqual(paired[-1]["unresolved"], 1)
        self.assertEqual(paired[-1]["both_certified"], 0)

    def test_resume_rejects_changed_and_duplicate_results(self):
        row = campaign.execute(self.case, "analytic", 10)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "results.jsonl"
            p.write_text(json.dumps(row)+"\n")
            self.assertEqual(len(campaign.checked_rows(p,[self.case],self.methods)),1)
            p.write_text((json.dumps(row)+"\n")*2)
            with self.assertRaises(ValueError):
                campaign.checked_rows(p,[self.case],self.methods)
            row["status"] = "TIMEOUT"
            p.write_text(json.dumps(row)+"\n")
            with self.assertRaises(ValueError):
                campaign.checked_rows(p,[self.case],self.methods)

    def test_end_to_end_resume_and_budget_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"source"
            (source/"inputs").mkdir(parents=True)
            file = source/"inputs/main.json"
            campaign.frozen_data().save_dataset(file,[self.old],{"synthetic":True})
            expected={"main":{"inputs":1, "identity_sha256":campaign.digest(
                [(self.old["case_id"],self.old["input_sha256"])])}}
            actual_read = campaign.read_json
            def read_json(path):
                return expected if path.name=="expected_inputs.json" else actual_read(path)
            out = Path(directory)/"out"
            with patch("experiments.same_slot.campaign.read_json",side_effect=read_json):
                # The test fixture has one approved synthetic case. No formal
                # identities are changed; the mock lives only in this test.
                first = campaign.run(source,out,"coverage",limit=1,timeout=10)
                self.assertEqual(first["status"],"COMPLETE")
                original = (out/"results.jsonl").read_bytes()
                resumed = campaign.run(source,out,"coverage",limit=1,timeout=10,resume=True)
                self.assertEqual(first,resumed)
                self.assertEqual(original,(out/"results.jsonl").read_bytes())
                with self.assertRaises(ValueError):
                    campaign.run(source,out,"coverage",limit=1,timeout=11,resume=True)


if __name__ == "__main__":
    unittest.main()
