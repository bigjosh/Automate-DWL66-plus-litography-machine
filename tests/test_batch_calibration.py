"""Verify automatic calibration and restart behavior without connecting to a machine."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from batch_calibration import BatchCalibration, farthest_disks
from batch_state import BatchState
from plate_transform import load_plate_calibration, plate_position


class BatchCalibrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.run = self.folder / "run-list.txt"
        self.skip = self.folder / "skip-list.txt"
        self.path = self.folder / "plate-calibration.json"
        self.run.write_text("(1,2)\n(5,5)\n(0,0)\n", encoding="utf-8")
        self.first, self.second = (5, 5), (0, 0)

    def batch(self):
        return BatchState(self.run, self.skip)

    def measure(self, plan, disk):
        x, y = plate_position(*disk, plan.spacing)
        stage = (x + 111.75, y - 25.125)
        plan.record_center(disk, f"X={stage[0]}, Y={stage[1]} µm", stage)

    def record_start(self, batch, disk):
        with batch.starting_exposure(disk):
            pass

    def test_farthest_tie_uses_first_pair_in_run_order(self):
        self.assertEqual(farthest_disks([(5, 0), (0, 0), (0, 5), (5, 5)]), ((5, 0), (0, 5)))
        self.assertEqual(farthest_disks([]), ())

    def test_selects_pending_disks_and_preserves_other_run_order(self):
        self.skip.write_text("(5,5)\n", encoding="utf-8")
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            self.assertEqual(plan.anchors, ((1, 2), (0, 0)))
            self.assertEqual(plan.positions, ((1, 2), (0, 0)))

    def test_recovered_center_and_confirmed_exposures_publish_standard_transform(self):
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            self.assertEqual(plan.positions, (self.first, self.second, (1, 2)))
            self.measure(plan, self.first)
            self.record_start(batch, self.first)
            self.assertFalse(self.path.exists())

        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            self.assertEqual(plan.positions, (self.second, (1, 2)))
            confirm = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                plan.resume(confirm)
            confirm.assert_called_once_with(self.first)
            plan.resume(lambda disk: disk == self.first)
            self.measure(plan, self.second)
            self.record_start(batch, self.second)
            self.assertFalse(self.path.exists())
            plan.finish_anchor(self.second)
            spacing, transform = load_plate_calibration(self.path)
            self.assertEqual(transform, plan.transform)
            x, y = plate_position(1, 2, spacing)
            self.assertEqual(transform.apply((x, y)), (x + 111.75, y - 25.125))
            points = json.loads(self.path.read_text(encoding="utf-8"))["calibration_points"]
            self.assertEqual(points[0]["located_xy_um"][1], -71247.625)

        self.path.unlink()  # Explicit removal means recalibrate from today's remaining disks.
        with self.batch() as batch:
            self.assertEqual(BatchCalibration(batch, self.path).anchors, ((1, 2),))

    def test_bad_checkpoint_or_changed_target_stops_before_motion(self):
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            with self.assertRaisesRegex(ValueError, "original --transform-file"):
                BatchCalibration(batch, self.folder / "other.json")
            with self.assertRaisesRegex(ValueError, "disagree"):
                plan.record_center(self.first, "X=1, Y=2 µm", (2, 1))
            record = batch.calibration_checkpoint()
            record["anchors"][0]["disk"] = [1, 2, 3]
            batch.save_calibration_checkpoint(record)
            with self.assertRaisesRegex(ValueError, "Invalid calibration checkpoint"):
                BatchCalibration(batch, self.path)

    def test_empty_pending_batch_recovers_missing_publication(self):
        self.run.write_text("(5,5)\n(0,0)\n", encoding="utf-8")
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            for disk in plan.anchors:
                self.measure(plan, disk)
                self.record_start(batch, disk)
                if disk == self.first:
                    plan.finish_anchor(disk)
            with patch("batch_calibration.save_calibration", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    plan.finish_anchor(self.second)

        with self.batch() as batch:
            self.assertEqual(batch.pending, ())
            plan = BatchCalibration(batch, self.path)
            plan.resume(lambda disk: self.fail("Both exposures already completed"))
            self.assertIsNotNone(plan.transform)
            self.assertTrue(self.path.exists())

    def test_recovery_after_publication_requires_the_matching_file(self):
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            for disk in plan.anchors:
                self.measure(plan, disk)
                self.record_start(batch, disk)
                if disk == self.first:
                    plan.finish_anchor(disk)
            # Interrupt the final progress write after the transform reached disk.
            original = batch.save_calibration_checkpoint
            def checkpoint(record):
                if record["closed"]:
                    raise OSError("interrupted")
                original(record)
            with patch.object(batch, "save_calibration_checkpoint", side_effect=checkpoint):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    plan.finish_anchor(self.second)

        with self.batch() as batch:
            published = self.path.read_text(encoding="utf-8")
            progress = self.skip.read_bytes()
            BatchCalibration.acknowledge_saved(batch, self.path, dry_run=True)
            self.assertEqual(self.skip.read_bytes(), progress)
            other = json.loads(published)
            other["calibration_points"][0]["ocr_text"] = "someone else's measurement"
            self.path.write_text(json.dumps(other), encoding="utf-8")
            for dry_run in (True, False):
                with self.assertRaisesRegex(RuntimeError, "unfinished calibration"):
                    BatchCalibration.acknowledge_saved(batch, self.path, dry_run=dry_run)
            self.path.write_text(published, encoding="utf-8")
            BatchCalibration.acknowledge_saved(batch, self.path)
            self.assertTrue(batch.calibration_checkpoint()["closed"])

    def test_file_appears_during_run_and_is_preserved(self):
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            self.path.write_bytes(b"operator's file")
            for disk in plan.anchors:
                self.measure(plan, disk)
                self.record_start(batch, disk)
                if disk == self.first:
                    plan.finish_anchor(disk)
            with self.assertRaises(FileExistsError):
                plan.finish_anchor(self.second)
            self.assertEqual(self.path.read_bytes(), b"operator's file")

    def test_single_disk_centers_without_publishing(self):
        self.run.write_text("(3,1)\n", encoding="utf-8")
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path)
            self.measure(plan, (3, 1))
            self.record_start(batch, (3, 1))
            plan.finish_anchor((3, 1))
            self.assertIsNone(plan.transform)
            self.assertFalse(self.path.exists())

    def test_dry_run_fits_but_never_marks_exposure_or_publishes(self):
        with self.batch() as batch:
            plan = BatchCalibration(batch, self.path, dry_run=True)
            for disk in plan.anchors:
                self.measure(plan, disk)
                plan.finish_anchor(disk)
            self.assertIsNotNone(plan.transform)
            self.assertFalse(self.path.exists())
            self.assertEqual(self.skip.read_bytes(), b"")

    def test_dry_run_reconciles_wet_exposure_before_proceeding_in_memory(self):
        with self.batch() as batch:
            wet = BatchCalibration(batch, self.path)
            self.measure(wet, self.first)
            self.record_start(batch, self.first)
        progress = self.skip.read_bytes()
        with self.batch() as batch:
            dry = BatchCalibration(batch, self.path, dry_run=True)
            self.assertEqual(dry.anchors, (self.first, self.second))
            declined = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                dry.resume(declined)
            declined.assert_called_once_with(self.first)
            self.assertEqual(self.skip.read_bytes(), progress)
            dry.resume(lambda disk: disk == self.first)
            self.assertEqual(dry.positions, (self.second, (1, 2)))
            self.measure(dry, self.second)
            dry.finish_anchor(self.second)
            self.assertIsNotNone(dry.transform)
        self.assertEqual(self.skip.read_bytes(), progress)
        self.assertFalse(self.path.exists())
        with self.batch() as batch:
            wet = BatchCalibration(batch, self.path)
            declined = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                wet.resume(declined)
            declined.assert_called_once_with(self.first)


if __name__ == "__main__":
    unittest.main()
