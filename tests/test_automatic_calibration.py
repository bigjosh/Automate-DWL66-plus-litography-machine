"""Missing-transform production runs with simulated machine input and temporary batch files."""

from pathlib import Path
from unittest.mock import Mock, patch

from plate_transform import load_plate_calibration, plate_position
from test_real_dwl_xy_gate import FakePSL, SimulatedBatchTest


def nominal_command(disk):
    return tuple(round(value) for value in plate_position(*disk))


def measured_center(disk):
    x, y = plate_position(*disk)
    # A plate translated, scaled and rotated relative to the stage.
    return (1.002 * x - 0.004 * y + 31.125, 0.004 * x + 1.002 * y - 12.375)


def calibrated_command(disk):
    return tuple(round(value) for value in measured_center(disk))


class AutomaticCalibrationTests(SimulatedBatchTest):
    def setUp(self):
        super().setUp()
        self.calibration.unlink()

    def machine(self, positions):
        centers = {nominal_command(disk): measured_center(disk) for disk in positions}

        def optical_read(index, command_x, command_y):
            x, y = centers[(command_x, command_y)]
            return f"X={x:.6f}, Y={y:.6f} um"

        return FakePSL(optical_results=optical_read)

    def set_run_list(self, positions):
        self.run_list.write_text("".join(f"({r},{c})\n" for r, c in positions), encoding="utf-8")

    def test_farthest_pending_pair_then_remaining_disks_in_run_order(self):
        skipped = (0, 0)
        pending = [(2, 2), (1, 5), (4, 5), (1, 1), (4, 1)]
        # Both diagonals have the same length. (1,5) appears first in the run list.
        anchors = [(1, 5), (4, 1)]
        others = [disk for disk in pending if disk not in anchors]
        self.set_run_list([skipped, *pending])
        self.skip_list.write_text("(0,0)\n", encoding="utf-8")
        fake = self.machine(anchors)
        original_open = Path.open

        def note_calibration_read(path, *args, **kwargs):
            if path == self.calibration and path.exists():
                fake.events.append(("load-calibration", len(fake.moves)))
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", note_calibration_read):
            self.run_script(fake, resume=True)

        self.assertEqual(fake.moves, [nominal_command(d) for d in anchors] +
                         [calibrated_command(d) for d in others])
        self.assertEqual(self.recorded_positions(), [skipped, *anchors, *others])
        self.assertIn(("load-calibration", 2), fake.events)
        self.assertEqual(fake.events.count(("click", "click-alignment-confirm")), 2)
        self.assertEqual(fake.events.count(("click", "focus-button")), len(pending) + 2)
        _, transform = load_plate_calibration(self.calibration)
        for disk in anchors + others:
            stage = transform.apply(plate_position(*disk))
            for actual, expected in zip(stage, measured_center(disk)):
                self.assertAlmostEqual(actual, expected, places=5)

        for index in range(2):
            start = fake.events.index(("read", "center", index))
            stop = fake.events.index(("read", "center", index + 1))
            cycle = fake.events[start:stop]
            expected_sequence = [
                ("read", "center", index), ("read", "move", index),
                ("click", "focus-button"), ("verify", "focus-executing"), ("verify", "focus-ready"),
                ("click", "click-alignment-confirm"), ("verify", "alignment-button-popped"),
                ("read", "optical", index),
                ("click", "focus-button"), ("verify", "focus-executing"), ("verify", "focus-ready"),
                ("click", "set-zero-button"), ("read", "zero", index),
                ("click", "laser-confirm"), ("verify", "job-finished"),
            ]
            self.assertEqual([event for event in cycle if event in expected_sequence], expected_sequence)

    def test_first_anchor_is_remembered_across_restart(self):
        disks = [(2, 3), (0, 5), (5, 0)]
        self.set_run_list(disks)
        fake = self.machine(disks)
        click = fake.click

        def stop_before_second_anchor(name, **options):
            if name == "center-button" and len(fake.moves) == 1:
                raise RuntimeError("between anchors")
            click(name, **options)

        with patch.object(fake, "click", side_effect=stop_before_second_anchor):
            with self.assertRaisesRegex(RuntimeError, "between anchors"):
                self.run_script(fake)
        self.assertEqual(self.recorded_positions(), [(0, 5)])
        self.assertFalse(self.calibration.exists())

        resumed = self.machine(disks)
        self.run_script(resumed, resume=True)
        self.assertEqual(resumed.moves, [nominal_command((5, 0)), calibrated_command((2, 3))])
        self.assertEqual(resumed.events.count(("click", "click-alignment-confirm")), 1)
        self.assertEqual(self.recorded_positions(), [(0, 5), (5, 0), (2, 3)])

    def test_started_second_anchor_needs_completion_before_publishing(self):
        disks = [(5, 1), (0, 4)]
        self.set_run_list(disks)
        fake = self.machine(disks)
        verify = fake.verifyFrame

        def skip_second_finish(name, **kwargs):
            matched = verify(name, **kwargs)
            return matched and not (name == "job-finished" and len(fake.moves) == 2)

        with patch.object(fake, "verifyFrame", side_effect=skip_second_finish):
            with self.assertRaises(RuntimeError):
                self.run_script(fake)
        self.assertEqual(self.recorded_positions(), disks)
        self.assertFalse(self.calibration.exists())

        decline = self.machine(disks)
        decline.alarm = Mock(return_value=1)
        with self.assertRaises((RuntimeError, SystemExit)):
            self.run_script(decline, resume=True)
        decline.alarm.assert_called_once()
        self.assertFalse(self.calibration.exists())
        self.assertEqual(decline.moves, [])

        confirmed = self.machine(disks)
        confirmed.alarm = Mock(return_value=0)
        self.run_script(confirmed, resume=True)
        confirmed.alarm.assert_called_once()
        self.assertEqual(confirmed.moves, [])
        self.assertNotIn(("click", "laser-confirm"), confirmed.events)
        _, transform = load_plate_calibration(self.calibration)
        for actual, expected in zip(transform.apply(plate_position(2, 2)), measured_center((2, 2))):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_center_saved_before_refocus_and_never_exposed_if_refocus_fails(self):
        disks = [(0, 1), (5, 4)]
        self.set_run_list(disks)
        fake = self.machine(disks)
        click = fake.click

        def require_saved_measurement_before_refocus(name, **options):
            if name == "focus-button" and fake.optical_reads:
                x, y = measured_center(disks[0])
                self.assertIn(f"X={x:.6f}, Y={y:.6f}", self.skip_list.read_text(encoding="utf-8"))
                fake.skip_check = "focus-ready"
            click(name, **options)

        with patch.object(fake, "click", side_effect=require_saved_measurement_before_refocus):
            with self.assertRaises(RuntimeError):
                self.run_script(fake)
        self.assertNotIn(("click", "set-zero-button"), fake.events)
        self.assertNotIn(("click", "laser-confirm"), fake.events)
        self.assertEqual(self.recorded_positions(), [])
        self.assertFalse(self.calibration.exists())

        resumed = self.machine(disks)
        x, y = measured_center(disks[0])
        second_disk_x, second_disk_y = measured_center(disks[1])
        resumed.optical_results = {
            0: f"X={x + 1}, Y={y} um",
            1: f"X={second_disk_x}, Y={second_disk_y} um",
        }
        self.run_script(resumed, resume=True)
        self.assertEqual(len(resumed.optical_reads), 2)
        _, transform = load_plate_calibration(self.calibration)
        self.assertAlmostEqual(transform.apply(plate_position(*disks[0]))[0], x + 1, places=5)

    def test_single_remaining_disk_is_centered_and_exposed_once(self):
        disk = (2, 1)
        self.set_run_list([disk])
        fake = self.machine([disk])
        self.run_script(fake)
        self.assertEqual(fake.moves, [nominal_command(disk)])
        self.assertEqual(fake.events.count(("click", "focus-button")), 2)
        self.assertEqual(self.recorded_positions(), [disk])
        self.assertFalse(self.calibration.exists())
        done = FakePSL()
        self.run_script(done, resume=True)
        self.assertFalse(any(event[0] in {"connect", "click"} for event in done.events))

    def test_missing_file_dry_run_keeps_measurements_and_transform_temporary(self):
        disks = [(2, 3), (0, 5), (5, 0)]
        self.set_run_list(disks)
        fake = self.machine(disks)
        self.run_script(fake, script=self.dry_run_copy())
        self.assertEqual(fake.moves, [nominal_command((0, 5)), nominal_command((5, 0)),
                                     calibrated_command((2, 3))])
        self.assertNotIn(("click", "laser-confirm"), fake.events)
        self.assertFalse(self.calibration.exists())
        self.assertEqual(self.skip_list.read_bytes(), b"")
