"""Check calibration reporting and the Windows entry point without machine input."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from plate_transform import SimilarityTransform, plate_position, save_calibration
from run_folders import create_run


PROJECT = Path(__file__).resolve().parents[1]


class CalibrationStatsTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.script = folder / "calibrate-stats.py"
        for name in [self.script.name, "plate_transform.py", "run_folders.py", "batch_state.py"]:
            shutil.copyfile(PROJECT / name, folder / name)
        (folder / "run-list.txt").write_text("(0,0)\n", encoding="utf-8")
        old = create_run(folder)
        self.write_calibration(old / "plate-calibration.json")
        self.cwd = folder / "different working folder"
        self.cwd.mkdir()
        self.default = create_run(folder) / "plate-calibration.json"

    def stats(self, *args):
        return subprocess.run([sys.executable, "-B", str(self.script), *map(str, args)],
                              cwd=self.cwd, capture_output=True, text=True, timeout=15)

    def write_calibration(self, path):
        spacing = 28000.0
        transform = SimilarityTransform(0.0, -1.0, 220.4, -350.123, 1.0125)
        anchors = []
        for row, col in [(0, 0), (5, 5)]:
            local = plate_position(row, col, spacing)
            anchors.append({"row": row, "col": col, "nominal_xy_um": local,
                            "located_xy_um": transform.apply(local)})
        expected = transform.apply(plate_position(0, 5, spacing))
        validation = {"row": 0, "col": 5, "expected_xy_um": expected,
                      "measured_xy_um": [expected[0] + 3, expected[1] + 4],
                      "error_x_um": 3.0, "error_y_um": 4.0, "error_distance_um": 5.0}
        save_calibration(path, transform, spacing=spacing, calibration_points=anchors,
                         validation=validation)

    def test_reads_latest_file_and_reports_measured_geometry(self):
        self.assertNotEqual(self.stats().returncode, 0)
        self.write_calibration(self.default)
        result = self.stats()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.default), result.stdout)
        for expected in ("28.350000 mm", "28.000000 mm", "1.012500000", "+220.400 um",
                         "-350.123 um", "-90.000000 deg", "(0,5)", "5.000 um (limit 500 um)"):
            self.assertIn(expected, result.stdout)

    def test_reads_explicit_path_and_reports_invalid_files(self):
        relative = Path("alternate transform.json")
        path = self.cwd / relative
        self.write_calibration(path)
        result = self.stats(relative)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(path), result.stdout)
        path.write_text("{broken", encoding="utf-8")
        for filename in [relative, self.default]:
            with self.subTest(filename=filename):
                result = self.stats(filename)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(filename), result.stderr)
                self.assertNotIn("Measured disk pitch", result.stdout)


@unittest.skipUnless(os.name == "nt", "Requires the Windows batch launcher")
class CalibrateLauncherTests(unittest.TestCase):
    def test_forwards_arguments_and_calibration_exit_code(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        project = root / "DWL test & setup"
        project.mkdir()
        launcher = project / "calibrate.bat"
        shutil.copyfile(PROJECT / launcher.name, launcher)
        (project / "calibrate-plate.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps(sys.argv))\n"
            "sys.exit(int(os.environ['DWL_TEST_EXIT_CODE']))\n", encoding="utf-8")
        # Only the harmless test command exists beside this copy of the launcher.
        args = ["--transform-file", "custom file.json", "--run-list", "selected disks.txt"]
        for status in (0, 7):
            with self.subTest(status=status):
                env = dict(os.environ, DWL_TEST_EXIT_CODE=str(status))
                env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
                result = subprocess.run(["cmd.exe", "/d", "/c", "call", str(launcher), *args],
                                        env=env, cwd=root, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=15)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(json.loads(result.stdout), [str(project / "calibrate-plate.py"),
                                                            "--latest-run", *args])
