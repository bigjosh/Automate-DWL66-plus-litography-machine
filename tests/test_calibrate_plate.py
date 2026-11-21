"""Offline calibration workflow checks; the fake accepts no exposure controls."""

import builtins
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from plate_transform import SimilarityTransform
from xy_readout import ReadoutError


SCRIPT = Path(__file__).resolve().parents[1] / "calibrate-plate.py"
DISKS = ((0, 0), (5, 5), (0, 5))


def load_script():
    spec = importlib.util.spec_from_file_location("calibrate_plate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    original_import = builtins.__import__

    def forbid_live_library(name, *args, **kwargs):
        if name == "playwrightscriptlib":
            raise AssertionError("Importing the tester must not import the live browser API")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=forbid_live_library):
        spec.loader.exec_module(module)
    return module


def nominal(row, col, spacing=28489.0):
    return ((row - 2.5) * spacing, -(col - 2.5) * spacing)


def transformed(point, angle_degrees=0.3, offset=(123.4, -56.7), scale=1.0):
    angle = math.radians(angle_degrees)
    x, y = point
    return (scale * (math.cos(angle) * x - math.sin(angle) * y) + offset[0],
            scale * (math.sin(angle) * x + math.cos(angle) * y) + offset[1])


class FakeMachine:
    """Model three center finds and forbid travel before a verified head raise."""

    CLICK_POINTS = {
        "global-alignment-tab", "move-absolute", "focus-button", "alignment-tab",
        "near-execute", "alignment-execute", "pre-click-alignment-confirm",
        "click-alignment-confirm", "laser-up-click", "center-button",
    }

    def __init__(self, *, third_error=(12.3, -4.5), spacing=28489.0, scale=1.0,
                 fail_verify=None, bad_reads=None, move_errors=None, margin_changes=None):
        self.events = []
        self.moves = []
        self.messages = []
        self.centers = [transformed(nominal(r, c, spacing), scale=scale)
                        for r, c in DISKS]
        self.centers[2] = tuple(a + b for a, b in zip(self.centers[2], third_error))
        self.fail_verify = fail_verify
        self.bad_reads = bad_reads or {}
        self.move_errors = move_errors or {}
        self.spacing = spacing
        self.margin_changes = margin_changes or {}
        self.verify_counts = {}
        self.center_count = 0
        self.pending_point = None
        self.pending_coordinates = {}
        self.field = None
        self.read_phase = None
        self.move_read_ok = False
        self.head_up = True
        self.head_up_verified = False

    def info(self, message, *, point=None):
        self.pending_point = point
        self.messages.append(str(message))
        self.events.append(("info", str(message)))

    def verifyFrame(self, name, **options):
        count = self.verify_counts.get(name, 0) + 1
        self.verify_counts[name] = count
        self.events.append(("verify", name, count))
        passed = (name, count) != self.fail_verify
        if passed and name in ("z-zero", "z-zero-check"):
            if not self.head_up:
                raise AssertionError("Cannot verify raised head while focused")
            self.head_up_verified = True
        return passed

    def _consume_point(self, name):
        if self.pending_point != name:
            raise AssertionError("Named input must identify its point in the preceding pause")
        self.pending_point = None

    def doubleClick(self, name):
        self._consume_point(name)
        if name not in ("x-field", "y-field"):
            raise AssertionError(f"Unexpected field {name}")
        self.field = name[0]
        self.events.append(("field", name))

    def sendkeys(self, text):
        if self.field is None:
            raise AssertionError("Typing requires selection of a coordinate field")
        value = int(text)
        self.pending_coordinates[self.field] = value
        self.events.append(("type", self.field, value))
        self.field = None

    def click(self, name):
        self._consume_point(name)
        if name not in self.CLICK_POINTS:
            raise AssertionError(f"Unapproved machine action: {name}")
        self.events.append(("click", name))
        if name == "center-button":
            if not self.head_up_verified or ("verify", "center-reset-options", 1) not in self.events:
                raise AssertionError("CENTER requires retracted head and checked reset options")
            self.read_phase = "frame"
        elif name == "move-absolute":
            if not self.head_up or not self.head_up_verified:
                raise AssertionError("Cross-disk travel before verified head raise")
            if set(self.pending_coordinates) != {"x", "y"}:
                raise AssertionError("Both coordinates must be supplied")
            self.moves.append(tuple(self.pending_coordinates[axis] for axis in "xy"))
            self.pending_coordinates.clear()
            self.move_read_ok = False
            self.read_phase = "move"
        elif name == "focus-button":
            if not self.move_read_ok:
                raise AssertionError("Focusing before a successful fresh move readback")
            self.head_up = self.head_up_verified = False
        elif name == "click-alignment-confirm":
            if self.head_up:
                raise AssertionError("Center finding without focus")
            self.center_count += 1
            self.read_phase = "center"
        elif name == "laser-up-click":
            self.head_up = True
            self.head_up_verified = False

    def wait(self, seconds):
        self.events.append(("wait", seconds))

    def grabOCRTextLine(self, name, *, tesseract_cmd, review, validator, retrycount, delay, margin,
                        initial_delay=None):
        if name != "actual-xy" or not review or not callable(validator):
            raise AssertionError("Calibration readbacks must use reviewed, validated actual-xy OCR")
        if self.read_phase == "frame":
            text = "X=0, Y=0 um"
            validator(text, margin)
            self.read_phase = None
            return text
        if self.read_phase not in ("move", "center"):
            raise AssertionError("OCR requires a pending move or completed center find")
        phase = self.read_phase
        index = len(self.moves) - 1
        center_margin = self.spacing / 2 if index < 2 else None
        if (retrycount, delay, margin) != (5, 5, 50 if phase == "move" else center_margin):
            raise AssertionError("Unexpected retry settings or default margin for this calibration read")
        if phase == "move":
            delta = self.move_errors.get(index, (0, 0))
            position = tuple(a + b for a, b in zip(self.moves[index], delta))
        else:
            position = self.centers[index]
            if ("verify", "alignment-button-popped", index + 1) not in self.events:
                raise AssertionError("Center read before completed alignment verification")
        text = self.bad_reads.get((phase, index),
                                  f"X={position[0]:.6f}, Y={position[1]:.6f} um")
        self.events.append(("read", phase, index, text))
        if margin is None:
            validator(text)
        else:
            validator(text, self.margin_changes.get((phase, index), float(margin)))
        if phase == "move":
            self.move_read_ok = True
        self.read_phase = None
        return text


class CalibrationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tester = load_script()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output_dir = Path(self.temp.name) / "calibration"

    def run_calibration(self, machine, **kwargs):
        return self.tester.run_calibration(
            machine, disks=DISKS, tesseract_cmd="fake-tesseract", output_dir=self.output_dir, **kwargs)

    def saved_report(self):
        return json.loads((self.output_dir / "result.json").read_text(encoding="utf-8"))

    def test_farthest_pair_and_most_perpendicular_disk_follow_run_order(self):
        all_disks = [(r, c) for r in range(6) for c in range(6)]
        self.assertEqual(self.tester.select_disks(all_disks), DISKS)
        self.assertEqual(self.tester.select_disks(all_disks[::-1]), ((5, 5), (0, 0), (5, 0)))
        subset = [(1, 4), (2, 2), (3, 3), (4, 4), (0, 0), (3, 1)]
        self.assertEqual(self.tester.select_disks(subset), ((4, 4), (0, 0), (1, 4)))

    def test_invalid_run_geometry_prevents_connection_and_preserves_previous_file(self):
        folder = Path(self.temp.name)
        run, skip, active = [folder / name for name in ("run.txt", "skip.txt", "active.json")]
        active.write_bytes(b"prior calibration")
        skip.write_bytes(b"# operator notes\n")
        for lines in ("(0,1)\n(2,2)", "(0,0)\n(1,1)\n(3,3)"):
            with self.subTest(lines=lines):
                run.write_text(lines, encoding="utf-8")
                with patch.object(self.tester, "TesseractReader") as reader, self.assertRaisesRegex(
                        ValueError, "collinear"):
                    self.tester.main(["--run-list", str(run), "--skip-list", str(skip),
                                      "--transform-file", str(active), "--output-dir", str(self.output_dir)])
                reader.assert_not_called()
                self.assertEqual(active.read_bytes(), b"prior calibration")
                self.assertEqual(skip.read_bytes(), b"# operator notes\n")
                self.assertFalse(self.output_dir.exists())

    def test_cli_uses_full_run_list_and_forwards_published_file_to_stats(self):
        folder = Path(self.temp.name)
        run, skip, active = [folder / name for name in ("run.txt", "skip.txt", "custom.json")]
        run.write_text("(1,4)\n(4,4)\n(0,0)\n(2,2)\n", encoding="utf-8")
        skip.write_bytes(b"(1,4)\n(4,4)\n")
        machine = Mock()
        with (patch.dict(sys.modules, {"playwrightscriptlib": machine}),
              patch.object(self.tester, "TesseractReader"),
              patch.object(self.tester, "run_calibration") as calibrate,
              patch.object(self.tester.subprocess, "run") as stats):
            stats.return_value.returncode = 7
            result = self.tester.main(["--run-list", str(run), "--skip-list", str(skip),
                                       "--transform-file", str(active), "--output-dir", str(self.output_dir)])
        self.assertEqual(calibrate.call_args.kwargs["disks"], ((4, 4), (0, 0), (1, 4)))
        self.assertEqual(result, 7)
        stats.assert_called_once_with([sys.executable, str(SCRIPT.with_name("calibrate-stats.py")), str(active)])
        machine.disconnect.assert_called_once()
        self.assertEqual(skip.read_bytes(), b"(1,4)\n(4,4)\n")

    def test_held_out_error_uses_radial_limit_and_failed_recalibration_retires_old_file(self):
        machine = FakeMachine(third_error=(400, 400))
        active = Path(self.temp.name) / "active.json"
        active.write_bytes(b"previous calibration")
        with self.assertRaisesRegex(ValueError, r"center error .* exceeds 500 um"):
            self.run_calibration(machine, transform_file=active)
        self.assertEqual(self.saved_report()["status"], "stopped")
        self.assertAlmostEqual(self.saved_report()["validation"]["error_distance_um"],
                               math.hypot(400, 400), places=5)
        self.assertTrue(machine.head_up_verified)
        self.assertFalse(active.exists())
        self.assertEqual((self.output_dir / self.tester.BACKUP).read_bytes(), b"previous calibration")

    def test_exact_radial_limit_from_unrounded_prediction_passes(self):
        machine = FakeMachine()
        machine.centers = [nominal(*disk) for disk in DISKS]
        x, y = machine.centers[2]
        machine.centers[2] = (x - 300, y - 400)
        report = self.run_calibration(machine)
        self.assertEqual(report["validation"]["error_distance_um"], 500.0)
        self.assertTrue((self.output_dir / "plate-calibration.json").exists())

    def test_reserved_artifact_names_are_rejected_before_machine_actions(self):
        for name in ("result.json", self.tester.CANDIDATE, self.tester.BACKUP):
            with self.subTest(name=name):
                machine = FakeMachine()
                with self.assertRaisesRegex(ValueError, "separate from"):
                    self.run_calibration(machine, transform_file=self.output_dir / name)
                self.assertFalse(machine.events)
        with self.assertRaisesRegex(ValueError, "Run and skip lists"):
            self.tester.main(["--run-list", str(self.output_dir / "run.log"),
                              "--output-dir", str(self.output_dir)])
        self.assertFalse(self.output_dir.exists())

    def test_two_corner_fit_predicts_third_and_reports_independent_error(self):
        machine = FakeMachine()
        report = self.run_calibration(machine)

        expected = transformed(nominal(0, 5))
        self.assertEqual(machine.moves[:2], [(-71222, 71222), (71222, -71222)])
        self.assertEqual(machine.moves[2], tuple(round(v) for v in expected))
        self.assertEqual(machine.center_count, 3)
        self.assertEqual(report["status"], "complete")
        self.assertAlmostEqual(report["transform"]["rotation_degrees"], 0.3, places=8)
        for value, target in zip(report["transform"]["translation_um"], (123.4, -56.7)):
            self.assertAlmostEqual(value, target, places=5)
        validation = report["validation"]
        self.assertAlmostEqual(validation["error_x_um"], 12.3, places=5)
        self.assertAlmostEqual(validation["error_y_um"], -4.5, places=5)
        self.assertAlmostEqual(validation["error_distance_um"], math.hypot(12.3, 4.5), places=5)
        for value, target in zip(validation["expected_xy_um"], expected):
            self.assertAlmostEqual(value, target, places=5)
        self.assertEqual(tuple(validation["commanded_xy_um"]), machine.moves[2])
        self.assertEqual(self.saved_report(), json.loads(json.dumps(report)))

    def test_each_move_is_verified_before_focus_and_head_raised_after_each_measurement(self):
        machine = FakeMachine()
        self.run_calibration(machine)

        self.assertEqual(machine.events.count(("click", "focus-button")), 3)
        self.assertEqual(machine.events.count(("click", "laser-up-click")), 3)
        self.assertTrue(machine.head_up_verified)
        for index in range(3):
            move_read = next(i for i, e in enumerate(machine.events)
                             if e[:3] == ("read", "move", index))
            preceding_move = max(i for i, e in enumerate(machine.events[:move_read])
                                 if e == ("click", "move-absolute"))
            self.assertIn(("wait", 5), machine.events[preceding_move:move_read])
            focus = next(i for i in range(move_read + 1, len(machine.events))
                         if machine.events[i] == ("click", "focus-button"))
            center_read = next(i for i, e in enumerate(machine.events)
                               if e[:3] == ("read", "center", index))
            self.assertLess(focus, center_read)
            self.assertIn(("verify", "focus-ready", index + 1),
                          machine.events[focus:center_read])
            self.assertIn(("verify", "alignment-button-pushed", index + 1),
                          machine.events[focus:center_read])

    def test_measurements_keep_decimal_values_and_raw_ocr(self):
        machine = FakeMachine()
        report = self.run_calibration(machine)
        for record, expected, corner in zip(report["measurements"], machine.centers,
                                             ((0, 0), (5, 5), (0, 5))):
            self.assertEqual((record["row"], record["col"]), corner)
            self.assertEqual(tuple(record["nominal_xy_um"]), nominal(*corner))
            self.assertTrue(record["ocr_text"].startswith("X="))
            for actual, target in zip(record["located_xy_um"], expected):
                self.assertAlmostEqual(actual, target, places=5)
        self.assertNotEqual(report["measurements"][0]["located_xy_um"][0],
                            round(report["measurements"][0]["located_xy_um"][0]))

    def test_failed_move_read_blocks_focus_and_further_motion(self):
        for index, delta in enumerate(((50.1, 0), (0, -50.1))):
            with self.subTest(delta=delta):
                self.output_dir = Path(self.temp.name) / f"calibration-{index}"
                machine = FakeMachine(move_errors={0: delta})
                with self.assertRaises(ReadoutError):
                    self.run_calibration(machine)
                self.assertEqual(len(machine.moves), 1)
                self.assertNotIn(("click", "focus-button"), machine.events)
                self.assertNotIn(("click", "laser-up-click"), machine.events)

    def test_exact_move_tolerance_remains_inclusive(self):
        machine = FakeMachine(move_errors={0: (50, -50)})
        self.assertEqual(self.run_calibration(machine)["status"], "complete")

    def test_adjusted_move_margin_preserves_fractional_displacement(self):
        machine = FakeMachine(move_errors={0: (50.125, 0)}, margin_changes={("move", 0): 50.125})
        report = self.run_calibration(machine)
        first = report["measurements"][0]
        self.assertEqual(first["move_readback_xy_um"][0] - first["commanded_xy_um"][0], 50.125)

    def test_adjusted_center_margin_uses_strict_radial_distance(self):
        machine = FakeMachine(margin_changes={("center", 0): 5})
        x, y = map(round, nominal(0, 0))
        machine.centers[0] = (x + 3, y + 4)
        with self.assertRaisesRegex(ReadoutError, "radial distance must be less than 5"):
            self.run_calibration(machine)
        self.assertEqual(len(machine.moves), 1)

    def test_malformed_decimal_measurement_blocks_next_motion(self):
        machine = FakeMachine(bad_reads={("center", 0): "X=-71234 7, Y=71000.0 um"})
        with self.assertRaises(ReadoutError):
            self.run_calibration(machine)
        self.assertEqual(len(machine.moves), 1)
        self.assertNotIn(("click", "laser-up-click"), machine.events)

    def test_zeroed_coordinates_after_center_find_are_rejected(self):
        machine = FakeMachine(bad_reads={("center", 0): "X=0, Y=0 um"})
        with self.assertRaises(ReadoutError):
            self.run_calibration(machine)
        self.assertEqual(len(machine.moves), 1)
        self.assertNotIn(("click", "laser-up-click"), machine.events)

    def test_skipping_initial_head_check_does_not_allow_a_move(self):
        machine = FakeMachine(fail_verify=("z-zero", 1))
        with self.assertRaises(RuntimeError):
            self.run_calibration(machine)
        self.assertFalse(machine.moves)
        self.assertFalse(any(e[0] in ("click", "type") for e in machine.events))

    def test_failed_focus_ready_does_not_start_alignment(self):
        machine = FakeMachine(fail_verify=("focus-ready", 1))
        with self.assertRaises(RuntimeError):
            self.run_calibration(machine)
        self.assertEqual(machine.center_count, 0)
        self.assertNotIn(("click", "alignment-execute"), machine.events)

    def test_failed_alignment_completion_does_not_record_a_center(self):
        machine = FakeMachine(fail_verify=("alignment-button-popped", 1))
        with self.assertRaises(RuntimeError):
            self.run_calibration(machine)
        self.assertFalse(any(e[:2] == ("read", "center") for e in machine.events))
        self.assertEqual(len(machine.moves), 1)

    def test_partial_measurement_survives_failed_head_raise(self):
        machine = FakeMachine(fail_verify=("z-zero", 3))
        with self.assertRaises(RuntimeError):
            self.run_calibration(machine)
        report = self.saved_report()
        self.assertNotEqual(report["status"], "complete")
        self.assertEqual(len(report["measurements"]), 1)
        self.assertEqual(len(machine.moves), 1)

    def test_final_head_raise_must_pass_before_report_is_complete(self):
        # CENTER is also checked before the three move-and-raise pairs.
        machine = FakeMachine(fail_verify=("z-zero", 7))
        with self.assertRaises(RuntimeError):
            self.run_calibration(machine)
        report = self.saved_report()
        self.assertNotEqual(report["status"], "complete")
        self.assertEqual(len(report["measurements"]), 3)
        self.assertEqual(len(machine.moves), 3)
        self.assertFalse((self.output_dir / "plate-calibration.json").exists())

    def test_third_prediction_uses_loaded_candidate_with_active_path_still_absent(self):
        machine = FakeMachine()
        original_loader = self.tester.load_plate_calibration
        original_apply = SimilarityTransform.apply
        candidates = []
        active = self.output_dir / "reusable-transform.json"

        def load_candidate(path):
            self.assertFalse(active.exists())
            spacing, loaded = original_loader(path)
            candidates.append(loaded)
            return spacing, loaded

        with (patch.object(self.tester, "load_plate_calibration", side_effect=load_candidate) as loader,
              patch.object(SimilarityTransform, "apply", autospec=True, side_effect=original_apply) as apply):
            report = self.run_calibration(machine, transform_file=active)

        loader.assert_called_once_with(self.output_dir / self.tester.CANDIDATE)
        self.assertTrue(any(call.args[0] is candidates[0] and call.args[1] == nominal(0, 5)
                            for call in apply.call_args_list))
        self.assertEqual(machine.moves[2], tuple(round(v) for v in transformed(nominal(0, 5))))
        self.assertAlmostEqual(report["validation"]["error_x_um"], 12.3, places=5)

    def test_unreadable_saved_transform_stops_before_third_move(self):
        machine = FakeMachine()
        with patch.object(self.tester, "load_plate_calibration", side_effect=ValueError("Bad transform")):
            with self.assertRaisesRegex(ValueError, "Bad transform"):
                self.run_calibration(machine)
        self.assertEqual(len(machine.moves), 2)
        self.assertTrue(machine.head_up_verified)
        self.assertEqual(len(self.saved_report()["measurements"]), 2)

    def test_reusable_file_keeps_two_fit_points_and_held_out_validation(self):
        machine = FakeMachine()
        report = self.run_calibration(machine)
        transform_file = self.output_dir / "plate-calibration.json"
        saved = json.loads(transform_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["model"], "similarity_2d")
        self.assertEqual(saved["units"], "um")
        self.assertEqual(saved["spacing_um"], 28489.0)
        self.assertEqual([(p["row"], p["col"]) for p in saved["calibration_points"]],
                         [(0, 0), (5, 5)])
        self.assertEqual(saved["validation"], json.loads(json.dumps(report["validation"])))
        loaded = self.tester.load_plate_calibration(transform_file)[1]
        for actual, expected in zip(loaded.apply(nominal(0, 5)),
                                    report["validation"]["expected_xy_um"]):
            self.assertAlmostEqual(actual, expected, places=10)

    def test_scaled_jig_fits_both_centers_and_preserves_independent_error(self):
        machine = FakeMachine(scale=1.0156)
        report = self.run_calibration(machine)
        self.assertAlmostEqual(report["transform"]["scale"], 1.0156, places=10)
        expected = transformed(nominal(0, 5), scale=1.0156)
        self.assertEqual(machine.moves[2], tuple(round(v) for v in expected))
        self.assertAlmostEqual(report["validation"]["error_x_um"], 12.3, places=5)
        self.assertAlmostEqual(report["validation"]["error_y_um"], -4.5, places=5)
        loaded = self.tester.load_plate_calibration(self.output_dir / "plate-calibration.json")[1]
        for record in report["measurements"][:2]:
            self.assertLess(math.dist(loaded.apply(record["nominal_xy_um"]),
                                      record["located_xy_um"]), 1e-7)
        for row, expected_row in zip(report["transform"]["transform_matrix"], loaded.matrix):
            for actual, expected_value in zip(row, expected_row):
                self.assertAlmostEqual(actual, expected_value, places=10)

    def test_altered_candidate_spacing_or_transform_blocks_validation_move(self):
        original_loader = self.tester.load_plate_calibration
        for field in ("scale", "spacing_um"):
            with self.subTest(field=field):
                machine = FakeMachine(scale=1.0156)
                self.output_dir = Path(self.temp.name) / field

                def change_saved_candidate(path):
                    spacing, loaded = original_loader(path)
                    if field == "spacing_um":
                        spacing *= 1.01
                    else:
                        loaded = SimilarityTransform(loaded.cos_theta, loaded.sin_theta,
                                                     loaded.tx, loaded.ty, loaded.scale * 1.01)
                    document = json.loads(Path(path).read_text(encoding="utf-8"))
                    self.tester.save_calibration(path, loaded, spacing=spacing,
                                                 calibration_points=document["calibration_points"])
                    return original_loader(path)

                with (patch.object(self.tester, "load_plate_calibration", side_effect=change_saved_candidate),
                      self.assertRaisesRegex(ValueError, "differs from the measured")):
                    self.run_calibration(machine)
                self.assertEqual(len(machine.moves), 2)
                self.assertTrue(machine.head_up_verified)
                self.assertFalse((self.output_dir / "plate-calibration.json").exists())

if __name__ == "__main__":
    unittest.main()
