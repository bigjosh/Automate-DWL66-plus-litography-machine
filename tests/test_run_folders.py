"""Run selection, initialization and Windows launch checks in a temporary project."""

from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from run_folders import active_run, create_run, latest_run


PROJECT = Path(__file__).resolve().parents[1]


class RunFolderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "DWL test & setup"
        self.project.mkdir()
        self.template = self.project / "run-list.txt"
        self.contents = b"\xef\xbb\xbf# ordered disks\r\n(1,3)\r\n(0,0)"
        self.template.write_bytes(self.contents)
        self.runs = self.project / "runs"
        self.clock = self.enterContext(patch("run_folders.datetime", wraps=datetime))
        self.clock.now.return_value = datetime(2026, 11, 20, 14, 51, 22)

    def test_new_run_snapshots_template_and_preserves_previous_carrier(self):
        original_skip = self.project / "skip-list.txt"
        original_fit = self.project / "plate-calibration.json"
        original_skip.write_bytes(b"(0,0)")
        original_fit.write_bytes(b"legacy transform")
        first = create_run(self.project)
        self.assertEqual(first, self.runs / "261120 145122")
        self.assertEqual({p.name for p in first.iterdir()}, {"run-list.txt", "skip-list.txt"})
        self.assertEqual((first / "run-list.txt").read_bytes(), self.contents)
        (first / "skip-list.txt").write_bytes(b"(1,3)\n")
        (first / "plate-calibration.json").write_bytes(b"first carrier")
        self.template.write_bytes(b"(5,5)\n")

        second = create_run(self.project)
        self.assertEqual(second, self.runs / "261120 145123")
        self.assertEqual(latest_run(self.project), second)
        self.assertEqual((second / "run-list.txt").read_bytes(), b"(5,5)\n")
        self.assertEqual((second / "skip-list.txt").read_bytes(), b"")
        self.assertFalse((second / "plate-calibration.json").exists())
        self.assertEqual((first / "run-list.txt").read_bytes(), self.contents)
        self.assertEqual((first / "skip-list.txt").read_bytes(), b"(1,3)\n")
        self.assertEqual((first / "plate-calibration.json").read_bytes(), b"first carrier")
        self.assertEqual(original_skip.read_bytes(), b"(0,0)")
        self.assertEqual(original_fit.read_bytes(), b"legacy transform")

    def test_newest_timestamp_wins_over_mtime_regardless_of_contents(self):
        old = create_run(self.project)
        for name in ("older", "plate-calibration-20261120", "261332 998888"):
            (self.runs / name).mkdir()
        newest = self.runs / "261121 000000"
        newest.mkdir()
        (newest / "screen.png").touch()
        os.utime(old, (2000000000, 2000000000))
        with self.assertRaisesRegex(FileNotFoundError, newest.name):
            latest_run(self.project)
        (newest / "run-list.txt").write_bytes(self.contents)
        (newest / "skip-list.txt").touch()
        self.assertEqual(latest_run(self.project), newest)

    def test_future_existing_folder_clock_rollback_and_file_collisions(self):
        self.runs.mkdir()
        log_folder = self.runs / "270101 000000"
        log_folder.mkdir()
        (log_folder / "screen.png").touch()
        (self.runs / "270101 000001").write_bytes(b"existing file")
        first = create_run(self.project)
        self.assertEqual(first.name, "270101 000002")
        self.clock.now.return_value = datetime(2026, 11, 19)
        second = create_run(self.project)
        self.assertEqual(second.name, "270101 000003")
        self.assertEqual(latest_run(self.project), second)
        self.assertEqual(list(log_folder.iterdir()), [log_folder / "screen.png"])
        self.assertEqual((self.runs / "270101 000001").read_bytes(), b"existing file")

    def test_active_run_blocks_new_or_competing_commands(self):
        first = create_run(self.project)
        with active_run(self.project) as selected:
            self.assertEqual(selected, first)
            with self.assertRaisesRegex(RuntimeError, "lock"):
                create_run(self.project)
            with self.assertRaisesRegex(RuntimeError, "lock"), active_run(self.project):
                self.fail("A second machine operation started")
        self.assertNotEqual(create_run(self.project), first)

    def test_damaged_latest_fails_without_replaying_an_older_carrier(self):
        create_run(self.project)
        second = create_run(self.project)
        skip = second / "skip-list.txt"
        skip.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "skip-list.txt"):
            latest_run(self.project)
        skip.write_bytes(b"bad exposure record")
        with self.assertRaisesRegex(ValueError, "skip-list.txt"):
            latest_run(self.project)

    def test_missing_or_invalid_template_never_creates_a_timestamp_folder(self):
        with self.assertRaisesRegex(FileNotFoundError, "new-run.bat"):
            latest_run(self.project)
        first = create_run(self.project)
        self.template.write_bytes(b"(9,0)\n")
        with self.assertRaisesRegex(ValueError, "run-list.txt"):
            create_run(self.project)
        self.template.unlink()
        with self.assertRaises(FileNotFoundError):
            create_run(self.project)
        self.assertEqual(latest_run(self.project), first)

    def test_interrupted_creation_leaves_last_complete_run_selected(self):
        first = create_run(self.project)
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                create_run(self.project)
        self.assertEqual(latest_run(self.project), first)
        self.assertEqual([p for p in self.runs.iterdir() if p.is_dir()], [first])

    @unittest.skipUnless(os.name == "nt", "Requires a Windows batch launcher")
    def test_launcher_creates_adjacent_run_from_another_working_directory(self):
        for name in ("new-run.bat", "run_folders.py", "batch_state.py"):
            shutil.copyfile(PROJECT / name, self.project / name)
        env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
        launch = ["cmd.exe", "/d", "/c", "call", str(self.project / "new-run.bat")]
        result = subprocess.run(launch, env=env, cwd=self.root, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("calibrate.bat", result.stdout)
        self.assertTrue((latest_run(self.project) / "run-list.txt").is_file())
        self.assertFalse((self.root / "runs").exists())
        before = list(self.runs.iterdir())
        unexpected = subprocess.run([*launch, "extra"], env=env, cwd=self.root,
                                    capture_output=True, text=True, timeout=15)
        self.assertNotEqual(unexpected.returncode, 0)
        self.assertEqual(list(self.runs.iterdir()), before)
