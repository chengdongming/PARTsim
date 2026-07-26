import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


B4_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B4_DIR))

import manifest_common as manifest
import run_manifest


class ManifestDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.manifest_path = Path(cls.temp_dir.name) / "pilot.jsonl"
        cls.manifest_path.write_bytes(manifest.render_manifest("pilot"))
        cls.records = manifest.validate_manifest(cls.manifest_path)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_limit_selects_first_n(self):
        plans = run_manifest.preview_records(self.records, "/tmp/out", 3)
        self.assertEqual(len(plans), 3)
        self.assertEqual(
            [plan["case_id"] for plan in plans],
            [record["case_id"] for record in self.records[:3]],
        )

    def test_output_root_does_not_change_identity(self):
        first = run_manifest.preview_records(self.records, "/tmp/a", 1)[0]
        second = run_manifest.preview_records(self.records, "/tmp/b", 1)[0]
        self.assertEqual(first["case_id"], second["case_id"])
        self.assertNotEqual(first["command_argv"], second["command_argv"])

    def test_dry_run_output_is_byte_stable(self):
        argv = [
            "--manifest",
            str(self.manifest_path),
            "--dry-run",
            "--limit",
            "3",
            "--output-root",
            "/tmp/out",
        ]
        outputs = []
        for _ in range(2):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(run_manifest.main(argv), 0)
            outputs.append(stream.getvalue().encode("utf-8"))
        self.assertEqual(outputs[0], outputs[1])

    def test_default_without_dry_run_is_preview_only(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = run_manifest.main(
                ["--manifest", str(self.manifest_path), "--limit", "1"]
            )
        self.assertEqual(status, 0)
        self.assertIn("command_argv", stream.getvalue())

    def test_execute_fails_explicitly(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = run_manifest.main(
                ["--manifest", str(self.manifest_path), "--execute"]
            )
        self.assertEqual(status, 2)
        self.assertEqual(stream.getvalue(), "execution is not implemented in I4A\n")

    def test_runner_does_not_import_subprocess(self):
        source = (B4_DIR / "run_manifest.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
