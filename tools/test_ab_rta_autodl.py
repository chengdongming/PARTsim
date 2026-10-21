"""Installer guards and interrupted-log recovery; no formal campaign is started."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import ab_rta_autodl as A


def args():
    return argparse.Namespace(workers=None, timing_workers=None, verification_workers=None, serial_timing=False)


class InstallerTests(unittest.TestCase):
    def test_default_and_serial_profiles(self):
        self.assertEqual(A.options(args()), {"workers":28,"timing_workers":28,"verification_workers":28})
        value=args();value.serial_timing=True
        self.assertEqual(A.options(value)["timing_workers"],1)
        old=A.options(args());new=args();new.workers=16
        with self.assertRaises(RuntimeError):A.options(new,old)
        self.assertEqual(A.options(args(),old),old)

    def test_two_installers_cannot_hold_same_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            with A.lock(Path(temp)/"lock"):
                with self.assertRaises(RuntimeError):
                    with A.lock(Path(temp)/"lock"):pass

    def test_status_marks_a_stale_running_file_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);A.write(root/"run_state.json",{"state":"RUNNING"})
            A.write(root/"results/campaign_status.json",{"state":"RUNNING","completed_requests":50})
            with patch.object(A,"active",return_value=False):self.assertEqual(A.status(root)["state"],"INTERRUPTED")
            with patch.object(A,"active",return_value=True):self.assertEqual(A.status(root)["state"],"RUNNING")

    def test_running_start_does_not_reinstall_or_launch(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(A,"active",return_value=True),patch.object(A,"setup",side_effect=AssertionError("Reinstalled a running job")):
            with contextlib.redirect_stdout(io.StringIO()):A.start(Path(temp),args())

    def records(self, root):
        path=root/"main/primary.jsonl";path.parent.mkdir(parents=True)
        rows=[{"request_id":str(i)*64,"status":"NO_CERTIFICATE"} for i in [1,2]]
        for row in rows:A.write(path.with_suffix(".records")/(row["request_id"]+".json"),row)
        return path,rows

    def test_partial_last_line_restored_from_atomic_copies_and_backup_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,rows=self.records(root)
            original=(A.canonical(rows[0])+"\n{\"request_id\":").encode();path.write_bytes(original)
            result=A.repair_indexes(root)
            self.assertEqual(len(result),1)
            self.assertEqual(Path(result[0]["backup"]).read_bytes(),original)
            self.assertEqual([json.loads(s) for s in path.read_text().splitlines()],rows)
            self.assertEqual(A.repair_indexes(root),[])

    def test_valid_last_line_without_newline_is_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,rows=self.records(root);path.write_text(A.canonical(rows[0]))
            A.repair_indexes(root)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(len(path.read_text().splitlines()),2)

    def test_conflicting_complete_row_is_never_silently_repaired(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,rows=self.records(root)
            original=(A.canonical({**rows[0],"status":"CERTIFIED"})+"\n{unfinished").encode();path.write_bytes(original)
            with self.assertRaises(RuntimeError):A.repair_indexes(root)
            self.assertEqual(path.read_bytes(),original)

    def test_middle_corruption_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,rows=self.records(root);path.write_bytes(b"not-json\n{unfinished")
            with self.assertRaises(RuntimeError):A.repair_indexes(root)

    def test_recovery_refuses_an_active_analysis_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.records(root)
            with A.lock(root/"main/run.lock"):
                with self.assertRaises(RuntimeError):A.repair_indexes(root)

    def test_worker_failure_is_recorded_even_before_campaign_starts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"logs").mkdir()
            with patch.object(A,"run_worker",side_effect=RuntimeError("test setup failure")):
                with self.assertRaises(RuntimeError):A.worker(root)
            self.assertEqual(A.read(root/"run_state.json")["state"],"FAILED")
            self.assertIn("test setup failure",(root/"logs/console.log").read_text())

    def test_pin_mismatch_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(A,"command",return_value=json.dumps({"source":"changed","packages":A.PACKAGES})):
                with self.assertRaises(RuntimeError):A.verify_environment(root)

    def test_real_sparse_checkout_pins_core_and_preserves_local_changes(self):
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);origin=base/"origin";origin.mkdir()
            # A complete small Git fixture avoids lazy-fetching from the development partial clone.
            for name in ["asapblock_f2","asapblock_unlimited"]:
                shutil.copytree(source/"experiments"/name,origin/"experiments"/name,
                                ignore=shutil.ignore_patterns("runs","__pycache__","*.pyc"))
            def git(*argv):
                return subprocess.check_output(["git","-C",str(origin),*argv],stderr=subprocess.DEVNULL,text=True).strip()
            git("init");git("add","--force","experiments")
            git("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-m","Complete source fixture")
            fixture=git("rev-parse","HEAD")
            root=base/"install";(root/"logs").mkdir(parents=True)
            with patch.object(A,"REPOSITORY",str(origin)),patch.object(A,"CORE_COMMIT",fixture),contextlib.redirect_stdout(io.StringIO()):
                repo=A.checkout(root)
                self.assertEqual(A.checkout(root),repo)
                target=repo/"experiments/asapblock_f2/README.md";target.write_text(target.read_text()+"\nlocal change\n")
                with self.assertRaises(RuntimeError):A.checkout(root)
                self.assertTrue(target.read_text().endswith("local change\n"))
            head=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
            self.assertEqual(head,fixture)
            self.assertTrue((repo/"experiments/asapblock_unlimited/runtime/frozen_sources.json").exists())
            (root/"venv/bin").mkdir(parents=True)
            (root/"venv/bin/python").symlink_to(sys.executable)
            A.verify_environment(root)

    def test_campaign_argv_is_full_scale_and_safe_for_spaces(self):
        root=Path("/tmp/with spaces;literal")
        argv=A.campaign_argv(root,A.options(args()))
        self.assertEqual(shlex.split(shlex.join(argv)),argv)
        self.assertNotIn("--limit",argv)
        self.assertEqual(argv[argv.index("--timing-workers")+1],"28")

    def test_package_is_readable_and_cannot_run_during_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);A.write(root/"install.json",{"core_commit":A.CORE_COMMIT})
            self.records(root/"results");(root/"logs").mkdir();(root/"logs/console.log").write_text("saved log\n")
            with patch.object(A,"active",return_value=True):
                with self.assertRaises(RuntimeError):A.pack(root)
            with patch.object(A,"active",return_value=False),contextlib.redirect_stdout(io.StringIO()):file=A.pack(root)
            with zipfile.ZipFile(file) as archive:
                self.assertIsNone(archive.testzip());self.assertIn("archive_status.json",archive.namelist())
                self.assertIn("logs/console.log",archive.namelist())
                self.assertTrue(any(x.startswith("results/main/primary.records/") for x in archive.namelist()))

    def test_pack_detects_a_running_core_even_without_a_tmux_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);A.write(root/"install.json",{"core_commit":A.CORE_COMMIT})
            with A.lock(root/"results/campaign.lock"),patch.object(A,"active",return_value=False):
                with self.assertRaises(RuntimeError):A.pack(root)


if __name__=="__main__":unittest.main(verbosity=2)
