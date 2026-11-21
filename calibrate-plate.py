"""Calibrate the loaded carrier using three disks from the run list.

Human-operated machine test: moves and focuses, but never starts an exposure.
Importing this file has no machine effects. All coordinates/results are in um.
"""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from batch_state import BatchState
from dwl_actions import (check_frame, configure_machine_screen, focus, optical_center_find,
                         raise_head, read_xy, restore_stage_frame, send_absolute_move)
from plate_transform import (VALIDATION_TOLERANCE_UM, fit_similarity_transform, load_plate_calibration,
                             load_validated_plate_calibration, plate_position, save_calibration)
from xy_readout import TesseractReader


PROJECT = Path(__file__).resolve().parent
CANDIDATE = "candidate-calibration.json"
BACKUP = "previous-plate-calibration.json"


def select_disks(positions):
    """Pick the longest baseline, then the disk farthest from that line."""
    if len(positions) < 3:
        raise ValueError("Calibration requires three noncollinear disks in the run list.")
    first, second = max(combinations(positions, 2), key=lambda pair: math.dist(*pair))
    dr, dc = second[0] - first[0], second[1] - first[1]

    def triangle_area(disk):
        # Twice the triangle area is the perpendicular distance times the fixed baseline.
        return abs(dr * (disk[1] - first[1]) - dc * (disk[0] - first[0]))

    third = max(positions, key=triangle_area)  # Ties follow the operator's run order.
    if triangle_area(third) == 0:
        raise ValueError("All run-list disks are collinear; include a disk off the baseline.")
    return first, second, third


def same_path(first, second):
    return (first.resolve() == second.resolve() or
            (first.exists() and second.exists() and first.samefile(second)))


def check_output_paths(output_dir, transform_file, *, batch_files=()):
    reserved = [output_dir / name for name in ("result.json", CANDIDATE, BACKUP)]
    artifacts = [*reserved, output_dir / "run.log"]
    if (transform_file == output_dir or transform_file.suffix.lower() != ".json" or
            any(same_path(transform_file, path) for path in artifacts)):
        raise ValueError("Transform file must be a .json file separate from the run artifacts.")
    for file in batch_files:
        if any(same_path(file, path) for path in [transform_file, *artifacts]):
            raise ValueError(f"Run and skip lists must be separate from calibration outputs: {file}")
    for path in reserved:
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Choose a new output directory; {path} already exists.")


def save_report(output_dir, report):
    """Replace the report atomically after each completed measurement."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_dir, suffix=".tmp",
            delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, output_dir / "result.json")
    finally:
        temporary.unlink(missing_ok=True)


def move_to(psl, target, tesseract_cmd):
    # Always verify the retracted head immediately before cross-disk travel.
    check_frame(psl, "z-zero", 0.999)
    check_frame(psl, "z-zero-check", 0.99)
    command = tuple(round(value) for value in target)
    send_absolute_move(psl, command)
    text, actual = read_xy(psl, tesseract_cmd, expected=command)
    psl.info(f"Move verified: X={actual[0]:.3f}, Y={actual[1]:.3f} um")
    return command, text, actual


def measure_disk(psl, disk, nominal, target, tesseract_cmd, *, role, spacing):
    row, col = disk
    psl.info(f"{role.title()} disk ({row},{col}): stage target X={target[0]:.3f}, Y={target[1]:.3f} um")
    command, move_text, moved_xy = move_to(psl, target, tesseract_cmd)
    focus(psl, "Focus on this disk before optical center finding")
    optical_center_find(psl)
    if role == "calibration":
        text, located = read_xy(psl, tesseract_cmd, center_near=command, max_center_shift=spacing / 2)
    else:
        # Report the measured holdout error, even when large. Its acceptance limit is fixed.
        text, located = read_xy(psl, tesseract_cmd)
    return {
        "role": role, "row": row, "col": col,
        "nominal_xy_um": nominal, "predicted_xy_um": target, "commanded_xy_um": command,
        "move_readback_xy_um": moved_xy, "move_ocr_text": move_text,
        "located_xy_um": located, "ocr_text": text,
    }


def fit_anchors(measurements):
    nominal = [m["nominal_xy_um"] for m in measurements]
    located = [m["located_xy_um"] for m in measurements]
    transform = fit_similarity_transform(*nominal, *located)
    nominal_distance = math.dist(*nominal)
    measured_distance = math.dist(*located)
    summary = {
        "scale": transform.scale, "rotation_degrees": transform.angle_degrees,
        "translation_um": [transform.tx, transform.ty],
        "rotation_matrix": [[transform.cos_theta, -transform.sin_theta],
                            [transform.sin_theta, transform.cos_theta]],
        "transform_matrix": transform.matrix, "nominal_baseline_um": nominal_distance,
        "measured_baseline_um": measured_distance, "baseline_error_um": measured_distance - nominal_distance,
        "scaled_baseline_error_um": measured_distance - transform.scale * nominal_distance,
        "fit_residuals_um": [[actual[i] - transform.apply(plate)[i] for i in (0, 1)]
                             for plate, actual in zip(nominal, located)],
    }
    return transform, summary


def validation_result(measurement):
    target = measurement["predicted_xy_um"]
    command = measurement["commanded_xy_um"]
    located = measurement["located_xy_um"]
    dx, dy = located[0] - target[0], located[1] - target[1]
    return {
        "row": measurement["row"], "col": measurement["col"],
        "expected_xy_um": target, "commanded_xy_um": command, "measured_xy_um": located,
        "error_x_um": dx, "error_y_um": dy, "error_distance_um": math.hypot(dx, dy),
        "commanded_error_x_um": located[0] - command[0], "commanded_error_y_um": located[1] - command[1],
        "tolerance_um": VALIDATION_TOLERANCE_UM,
    }


def run_calibration(psl, *, disks, tesseract_cmd, output_dir, spacing=28489.0, transform_file=None):
    """Measure in the production stage frame, publishing only after an independent pass."""
    disks = select_disks(disks)
    output_dir = Path(output_dir).resolve()
    transform_file = Path(transform_file or output_dir / "plate-calibration.json").resolve()
    check_output_paths(output_dir, transform_file)
    candidate = output_dir / CANDIDATE
    nominal = [plate_position(*disk, spacing=spacing) for disk in disks]
    report = {
        "version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress", "units": "um", "spacing_um": spacing,
        "model": "stage_xy = scale * R * plate_xy + translation; uniform scaling",
        "coordinate_frame": "CENTER with Reset Offset and Reset Rotation before first move",
        "transform_file": str(transform_file), "measurements": [],
    }
    save_report(output_dir, report)
    try:
        if transform_file.exists():
            # Archive across volumes before retiring the old production transform.
            shutil.copy2(transform_file, output_dir / BACKUP)
            transform_file.unlink()
        psl.info(f"Calibration disks {disks[0]} and {disks[1]}, validation disk {disks[2]}. "
                 "Start with the head raised. Use Find wafer center optical with its Set zero option OFF.")
        restore_stage_frame(psl, tesseract_cmd)
        measurements = report["measurements"]
        for disk, local in zip(disks[:2], nominal[:2]):
            measurements.append(measure_disk(psl, disk, local, local, tesseract_cmd,
                                             role="calibration", spacing=spacing))
            save_report(output_dir, report)
            raise_head(psl)
        anchors = measurements.copy()
        fitted, report["transform"] = fit_anchors(anchors)
        save_calibration(candidate, fitted, spacing=spacing, calibration_points=anchors, overwrite=False)
        save_report(output_dir, report)

        # Exercise the serialized fit. The production path is still absent.
        saved_spacing, transform = load_plate_calibration(candidate)
        if (saved_spacing, transform) != (spacing, fitted):
            raise ValueError("Saved candidate calibration differs from the measured two-disk fit.")
        target = transform.apply(nominal[2])
        third = measure_disk(psl, disks[2], nominal[2], target, tesseract_cmd,
                             role="validation", spacing=spacing)
        measurements.append(third)
        validation = report["validation"] = validation_result(third)
        save_report(output_dir, report)
        raise_head(psl)
        error = validation["error_distance_um"]
        if error > VALIDATION_TOLERANCE_UM:
            raise ValueError(f"Calibration failed at disk {disks[2]}: center error {error:.3f} um "
                             f"exceeds {VALIDATION_TOLERANCE_UM:g} um. Results: {output_dir / 'result.json'}")

        save_calibration(candidate, transform, spacing=spacing, calibration_points=anchors, validation=validation)
        load_validated_plate_calibration(candidate)
        psl.info(f"Calibration passed at {disks[2]}: dX={validation['error_x_um']:+.3f} um, "
                 f"dY={validation['error_y_um']:+.3f} um, distance={error:.3f} um. "
                 f"Head raised. Publishing calibration to {transform_file}.")
        save_calibration(transform_file, transform, spacing=spacing, calibration_points=anchors,
                         validation=validation, overwrite=False)
    except BaseException as exc:
        report["status"] = "stopped"
        report["error"] = f"{type(exc).__name__}: {exc}"
        save_report(output_dir, report)
        raise

    report["status"] = "complete"
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    save_report(output_dir, report)
    return report


def positive_spacing(value):
    try:
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ValueError
        plate_position(0, 0, result)
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Spacing must be a positive finite number") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
    parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe")
    parser.add_argument("--crd-popup-screenshot", type=Path,
                        help="Redefine the CRD popup guard using this screenshot")
    parser.add_argument("--spacing", type=positive_spacing, default=28489.0,
                        help="Plate grid spacing in um (default: 28489)")
    parser.add_argument("--output-dir", type=Path, help="New folder for results and screenshots")
    parser.add_argument("--run-list", type=Path, default=PROJECT / "run-list.txt",
                        help="Select three disks from this ordered run list")
    parser.add_argument("--skip-list", type=Path, default=PROJECT / "skip-list.txt",
                        help="Use the production skip list to lock this batch during calibration")
    parser.add_argument("--transform-file", type=Path,
                        default=PROJECT / "plate-calibration.json",
                        help="Reusable transform JSON (default: plate-calibration.json beside script)")
    args = parser.parse_args(argv)
    output_dir = (args.output_dir or PROJECT / "runs" /
                  ("plate-calibration-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    transform_file = args.transform_file.resolve()
    check_output_paths(output_dir, transform_file, batch_files=(args.run_list, args.skip_list))
    with BatchState(args.run_list, args.skip_list) as batch:
        disks = select_disks(batch.positions)
        reader = TesseractReader(args.tesseract_cmd)
        import playwrightscriptlib as psl

        output_dir.mkdir(parents=True, exist_ok=True)
        psl.logging(True, str(output_dir / "run.log"))
        psl.screenshotOnInfo(True, str(output_dir / "screenshots"))
        psl.alarmOnError()
        psl.connect(args.url, page_hint="remotedesktop.google.com")
        try:
            configure_machine_screen(psl, args.crd_popup_screenshot)
            run_calibration(psl, disks=disks, tesseract_cmd=reader.executable,
                            output_dir=output_dir, spacing=args.spacing, transform_file=transform_file)
        finally:
            psl.disconnect()
        return subprocess.run([sys.executable, str(PROJECT / "calibrate-stats.py"),
                               str(transform_file)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
