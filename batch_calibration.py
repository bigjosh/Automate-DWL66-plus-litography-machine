"""Plan and recover a two-disk plate calibration inside a production batch."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Optional

from plate_transform import (TWO_EXPOSED_ANCHORS, farthest_disks, fit_similarity_transform,
                             load_validated_plate_calibration, plate_position, save_calibration)
from xy_readout import parse_xy


def _stage_xy(text):
    xy = tuple(map(float, parse_xy(text)))
    if not all(map(math.isfinite, xy)):
        raise ValueError("The saved optical center must have finite coordinates.")
    return xy


@dataclass
class _Anchor:
    disk: tuple
    text: Optional[str] = None
    finished: bool = False

    def measurement(self, spacing):
        row, col = self.disk
        nominal = list(plate_position(row, col, spacing))
        return {"row": row, "col": col, "nominal_xy_um": nominal,
                "commanded_xy_um": list(map(round, nominal)),
                "located_xy_um": list(_stage_xy(self.text)), "ocr_text": self.text}


def _restore(batch, path):
    record = batch.calibration_checkpoint()
    if record is None:
        return None
    try:
        if type(record["version"]) is not int or record["version"] != 1 or type(record["closed"]) is not bool:
            raise ValueError("unsupported record")
        anchors = {}
        for item in record["anchors"]:
            disk = tuple(item["disk"])
            row, col = disk
            plate_position(row, col)  # Validate both grid indices before any movement.
            text, finished = item["text"], item["finished"]
            if text is not None:
                if not isinstance(text, str):
                    raise ValueError("invalid saved OCR text")
                _stage_xy(text)
            if disk in anchors or type(finished) is not bool or (finished and text is None):
                raise ValueError("invalid anchor")
            anchors[disk] = _Anchor(disk, text, finished)
        if len(anchors) not in (1, 2):
            raise ValueError("expected one or two anchors")
        if record["closed"]:
            if not all(anchor.finished for anchor in anchors.values()):
                raise ValueError("closed plan with incomplete anchors")
            return None
        if Path(record["target"]) != path:
            raise ValueError(f"use the original --transform-file {record['target']!r}")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"Invalid calibration checkpoint in {batch.skip_path}: {exc}") from exc
    return anchors


class BatchCalibration:
    """When calibration is missing, measure the selected disks before the rest."""

    spacing = 28489.0

    def __init__(self, batch, transform_path, *, dry_run=False):
        self._batch = batch
        self._path = Path(transform_path).resolve()
        self._dry_run = dry_run
        self._closed = False
        self.transform = None
        anchors = _restore(batch, self._path)
        if anchors is None:
            self._anchors = {disk: _Anchor(disk) for disk in farthest_disks(batch.pending)}
            if self._anchors:
                self._checkpoint()
        else:
            self._anchors = anchors
            for disk, anchor in anchors.items():
                if disk not in batch.positions:
                    raise ValueError(f"Calibration disk {disk} is missing from {batch.run_path}.")
                if anchor.finished and disk not in batch.skipped:
                    raise ValueError(f"Completed calibration disk {disk} is missing from {batch.skip_path}.")
                if disk not in batch.pending and (disk not in batch.skipped or anchor.text is None):
                    raise ValueError(f"Calibration disk {disk} is unavailable. Check the run and skip lists.")
            if len(anchors) == 1 and any(disk not in anchors for disk in batch.pending):
                raise ValueError("The saved one-disk calibration plan has additional pending disks. "
                                 "Restore the original run list before resuming.")

    @property
    def anchors(self):
        return tuple(self._anchors)

    @property
    def positions(self):
        pending = self._batch.pending
        return (tuple(disk for disk in self.anchors if disk in pending) +
                tuple(disk for disk in pending if disk not in self._anchors))

    def _checkpoint(self, *, closed=False):
        if not self._dry_run:
            self._batch.save_calibration_checkpoint({
                "version": 1, "target": str(self._path), "closed": closed,
                "anchors": [vars(anchor) for anchor in self._anchors.values()]})

    def record_center(self, position, text, stage_xy):
        """Save the measured stage readout before refocusing or changing the origin."""
        anchor = self._anchors[position]
        if anchor.finished or position in self._batch.skipped:
            raise RuntimeError(f"Calibration disk {position} has already started exposure.")
        if _stage_xy(text) != tuple(map(float, stage_xy)):
            raise ValueError("The optical center coordinates disagree with their OCR text.")
        anchor.text = text
        self._checkpoint()

    def finish_anchor(self, position):
        """Called only after job-finished (or at the corresponding dry-run step)."""
        anchor = self._anchors[position]
        if anchor.text is None or (not self._dry_run and position not in self._batch.skipped):
            raise RuntimeError(f"Calibration disk {position} needs a measured center and exposure start.")
        if not anchor.finished:
            anchor.finished = True
            self._checkpoint()
        self._publish_if_ready()

    def resume(self, confirm_finished):
        """Reconcile starts without completion records before further machine actions."""
        for disk, anchor in self._anchors.items():
            if disk in self._batch.skipped and not anchor.finished:
                if confirm_finished(disk) is not True:
                    raise RuntimeError(f"Exposure completion for calibration disk {disk} is unresolved.")
                anchor.finished = True
                self._checkpoint()
        self._publish_if_ready()

    def _fit(self):
        first, second = [a.measurement(self.spacing) for a in self._anchors.values()]
        transform = fit_similarity_transform(first["nominal_xy_um"], second["nominal_xy_um"],
                                             first["located_xy_um"], second["located_xy_um"])
        return transform, [first, second]

    def _publish_if_ready(self):
        if self._closed or not self._anchors or not all(a.finished for a in self._anchors.values()):
            return
        if len(self._anchors) == 2:
            transform, points = self._fit()
            if not self._dry_run:
                save_calibration(self._path, transform, spacing=self.spacing,
                                 calibration_points=points, validation={"method": TWO_EXPOSED_ANCHORS},
                                 overwrite=False)
                spacing, saved_transform = load_validated_plate_calibration(self._path)
                if (spacing, saved_transform) != (self.spacing, transform):
                    raise RuntimeError(f"Saved calibration {self._path} changed before it could be used.")
                transform = saved_transform
            self.transform = transform
        self._checkpoint(closed=True)
        self._closed = True

    @classmethod
    def acknowledge_saved(cls, batch, transform_path, *, dry_run=False):
        """Recover a stop between file publication and closing its checkpoint."""
        path = Path(transform_path).resolve()
        anchors = _restore(batch, path)
        if anchors is None:
            return
        plan = cls(batch, path, dry_run=dry_run)
        if len(anchors) == 2 and all(a.finished for a in anchors.values()):
            expected, points = plan._fit()
            document = json.loads(path.read_text(encoding="utf-8"))
            if (document.get("validation") == {"method": TWO_EXPOSED_ANCHORS}
                    and document.get("calibration_points") == points
                    and load_validated_plate_calibration(path) == (plan.spacing, expected)):
                plan._checkpoint(closed=True)
                return
        raise RuntimeError(f"{path} exists alongside an unfinished calibration in {batch.skip_path}. "
                           "Check the calibration file and anchor exposure history before resuming.")
