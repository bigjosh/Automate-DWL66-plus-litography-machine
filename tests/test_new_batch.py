"""Exercise the Windows reset launcher only against temporary batch files."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from batch_state import BatchState


@unittest.skipUnless(os.name == "nt", "Requires Windows batch files")
class NewBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.project = root / "DWL test & setup"
        self.project.mkdir()
        self.cwd = root / "other batch"
        self.cwd.mkdir()
        self.launcher = self.project / "new-batch.bat"
        shutil.copyfile(Path(__file__).resolve().parents[1] / self.launcher.name, self.launcher)
        self.run_list = self.project / "run-list.txt"
        self.skip = self.project / "skip-list.txt"
        self.calibration = self.project / "plate-calibration.json"
        self.run_list.write_bytes(b"(0,0)\n(0,1)\n")
        self.skip.write_bytes(b"(0,0)\n")
        self.calibration.write_bytes(b"previous carrier")

    def launch(self):
        return subprocess.run(
            ["cmd.exe", "/d", "/c", "call", str(self.launcher)],
            cwd=self.cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
        )

    def test_resets_only_adjacent_batch_files_and_accepts_an_empty_batch(self):
        unrelated = [self.project / "backup-calibration.json", self.cwd / self.skip.name,
                     self.cwd / self.calibration.name]
        for path in unrelated:
            path.write_bytes(b"keep")
        for _ in range(2):
            result = self.launch()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("calibrate.bat", result.stdout)
            self.assertFalse(self.skip.exists())
            self.assertFalse(self.calibration.exists())
            self.assertEqual(self.run_list.read_bytes(), b"(0,0)\n(0,1)\n")
            for path in unrelated:
                self.assertEqual(path.read_bytes(), b"keep")

    def test_active_batch_keeps_both_files(self):
        with BatchState(self.run_list, self.skip):
            result = self.launch()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.skip.read_bytes(), b"(0,0)\n")
            self.assertEqual(self.calibration.read_bytes(), b"previous carrier")

    def test_calibration_deletion_failure_preserves_progress(self):
        with self.calibration.open("rb"):
            result = self.launch()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.skip.read_bytes(), b"(0,0)\n")
            self.assertEqual(self.calibration.read_bytes(), b"previous carrier")
