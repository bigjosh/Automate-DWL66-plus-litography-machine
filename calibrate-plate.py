"""Measure a two-corner plate transform and independently check a third corner.

Human-operated machine test: moves and focuses, but never starts an exposure.
Importing this file has no machine effects. All coordinates/results are in um.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from plate_transform import (fit_similarity_transform, load_calibration, plate_position,
                             save_calibration)
from xy_readout import ReadoutError, TesseractReader, parse_xy


CALIBRATION_CORNERS = ((0, 0), (5, 5))
VALIDATION_CORNER = (0, 5)
MOVE_TOLERANCE_UM = 50


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


def check_frame(psl, name, match_level, **options):
    psl.info(f"Check {name}")
    matched = psl.verifyFrame(name, matchLevel=match_level, **options)
    if matched is not True:
        raise RuntimeError(
            f"Required check {name!r} was skipped/aborted. Calibration stopped.")


def click(psl, name, message=None, *, double=False):
    psl.info(message or f"Click {name}", point=name)
    (psl.doubleClick if double else psl.click)(name)


def read_xy(psl, tesseract_cmd, *, expected=None, center_near=None,
            max_center_shift=None):
    """Preserve decimal measurements; reject ambiguous OCR rather than repair it."""
    def validate(text):
        values = parse_xy(text)
        if not all(math.isfinite(float(value)) for value in values):
            raise ReadoutError("X/Y readout is outside the finite numeric range.")
        if expected is not None:
            errors = [abs(value - Decimal(str(target)))
                      for value, target in zip(values, expected)]
            if any(error > MOVE_TOLERANCE_UM for error in errors):
                raise ReadoutError(
                    f"Move check failed: read {values}, commanded {expected}; "
                    f"must be within +/-{MOVE_TOLERANCE_UM} um on each axis.")
        if center_near is not None:
            shift = math.dist(tuple(map(float, values)), center_near)
            if shift >= max_center_shift:
                raise ReadoutError(
                    f"Found center is {shift:.1f} um from the commanded position "
                    f"(must be less than {max_center_shift:.1f} um). Check that "
                    "center finding did not zero/change the coordinates and that "
                    "the intended disk was found.")
        return values

    text = psl.grabOCRTextLine(
        "actual-xy", tesseract_cmd=tesseract_cmd, review=True,
        validator=validate)
    return text, tuple(map(float, validate(text)))


def move_to(psl, target, tesseract_cmd):
    # Always verify the retracted head immediately before cross-disk travel.
    check_frame(psl, "z-zero", 0.999)
    check_frame(psl, "z-zero-check", 0.99)
    click(psl, "global-alignment-tab")
    psl.wait(1)
    command = tuple(round(value) for value in target)
    for axis, value in zip(("x", "y"), command):
        check_frame(psl, f"check{axis}-field-label", 0.99)
        click(psl, f"{axis}-field", double=True)
        psl.info(f"Type {axis.upper()}={value} um")
        psl.sendkeys(str(value))
    check_frame(psl, "move-absolute-check", 0.99)
    click(psl, "move-absolute", f"Move absolute to X={command[0]}, Y={command[1]} um")
    psl.wait(5)
    text, actual = read_xy(psl, tesseract_cmd, expected=command)
    psl.info(f"Move verified: X={actual[0]:.3f}, Y={actual[1]:.3f} um")
    return command, text, actual


def find_center(psl):
    # Same focus/alignment sequence and thresholds as the working production run.
    click(psl, "focus-button", "Focus on this disk before optical center finding")
    psl.wait(5)
    check_frame(psl, "focus-executing", 0.96, delay=10, retrycount=24)
    check_frame(psl, "focus-ready", 0.96, delay=10, retrycount=24)
    click(psl, "alignment-tab")
    check_frame(psl, "alignment-execute-image", 0.999)
    click(psl, "near-execute", "Focus the alignment panel next to Execute")
    click(psl, "alignment-execute")
    psl.wait(1)
    check_frame(psl, "check-alignment-confirm", 0.99, delay=10, retrycount=24)
    click(psl, "pre-click-alignment-confirm")
    click(psl, "click-alignment-confirm", "Confirm Find wafer center optical")
    psl.wait(1)
    check_frame(psl, "alignment-button-pushed", 0.9, delay=10, retrycount=10)
    check_frame(psl, "alignment-button-popped", 0.9, delay=10, retrycount=10)
    psl.wait(5)


def raise_head(psl):
    click(psl, "laser-up-click", "Raise the head after measuring this disk")
    check_frame(psl, "z-zero", 0.999, delay=10, retrycount=78)


def run_calibration(psl, *, tesseract_cmd, output_dir, spacing=28489.0,
                    transform_file=None):
    """Use an initialized PSL connection with the existing layout and click guard."""
    output_dir = Path(output_dir).resolve()
    transform_file = Path(transform_file or output_dir / "plate-calibration.json").resolve()
    if transform_file == output_dir / "result.json" or transform_file.suffix.lower() != ".json":
        raise ValueError("Transform file must be a .json file separate from result.json")
    corners = (*CALIBRATION_CORNERS, VALIDATION_CORNER)
    nominal = [plate_position(*corner, spacing=spacing) for corner in corners]
    if (output_dir / "result.json").exists():
        raise FileExistsError(f"Choose a new output folder; result.json exists in {output_dir}")
    report = {
        "version": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
        "units": "um",
        "spacing_um": spacing,
        "model": "stage_xy = scale * R * plate_xy + translation; uniform scaling",
        "coordinate_frame": "unchanged stage readout; no origin/rotation reset",
        "transform_file": str(transform_file),
        "measurements": [],
    }
    save_report(output_dir, report)
    transform = None
    try:
        psl.info(
            "Plate calibration: (0,0) and (5,5), validation at (0,5). "
            "Use Find wafer center optical with coordinate zeroing OFF. "
            "Keep the stage origin/rotation unchanged throughout. "
            "Start with the head raised and the usual nominal plate coordinates. "
            "This test moves and focuses; it does not expose.")
        for index, (row, col) in enumerate(corners):
            role = "calibration" if index < 2 else "validation"
            if index == 2:
                # Exercise the same persisted transform loader production can use.
                transform = load_calibration(transform_file)
            target = nominal[index] if index < 2 else transform.apply(nominal[index])
            psl.info(
                f"{role.title()} disk ({row},{col}): target "
                f"X={target[0]:.3f}, Y={target[1]:.3f} um")
            command, move_text, moved_xy = move_to(psl, target, tesseract_cmd)
            find_center(psl)
            text, located = read_xy(
                psl, tesseract_cmd, center_near=command,
                max_center_shift=spacing / 2)
            report["measurements"].append({
                "role": role, "row": row, "col": col,
                "nominal_xy_um": nominal[index], "predicted_xy_um": target,
                "commanded_xy_um": command, "move_readback_xy_um": moved_xy,
                "move_ocr_text": move_text, "located_xy_um": located,
                "ocr_text": text,
            })
            # Persist the center before any operator pause or subsequent motion.
            save_report(output_dir, report)
            if index == 1:
                stage_a, stage_b = [m["located_xy_um"] for m in report["measurements"]]
                transform = fit_similarity_transform(nominal[0], nominal[1], stage_a, stage_b)
                nominal_distance = math.dist(nominal[0], nominal[1])
                measured_distance = math.dist(stage_a, stage_b)
                report["transform"] = {
                    "scale": transform.scale,
                    "rotation_degrees": transform.angle_degrees,
                    "translation_um": [transform.tx, transform.ty],
                    "rotation_matrix": [[transform.cos_theta, -transform.sin_theta],
                                        [transform.sin_theta, transform.cos_theta]],
                    "transform_matrix": transform.matrix,
                    "nominal_baseline_um": nominal_distance,
                    "measured_baseline_um": measured_distance,
                    "baseline_error_um": measured_distance - nominal_distance,
                    "scaled_baseline_error_um": measured_distance - transform.scale * nominal_distance,
                    "fit_residuals_um": [
                        [actual[axis] - transform.apply(plate)[axis] for axis in (0, 1)]
                        for plate, actual in zip(nominal[:2], (stage_a, stage_b))],
                }
                if transform_file.exists():
                    shutil.copy2(transform_file, output_dir / "previous-plate-calibration.json")
                save_calibration(
                    transform_file, transform, spacing=spacing,
                    calibration_points=report["measurements"][:2])
                save_report(output_dir, report)
                psl.info(
                    f"Plate transform: scale={transform.scale:.10f}; "
                    f"rotation={transform.angle_degrees:.6f} deg; "
                    f"translation X={transform.tx:.3f}, Y={transform.ty:.3f} um; "
                    f"baseline length difference={measured_distance - nominal_distance:+.3f} um. "
                    f"Saved to {transform_file}. The third corner will reload and check this file.")
            elif index == 2:
                dx, dy = located[0] - target[0], located[1] - target[1]
                report["validation"] = {
                    "row": row, "col": col,
                    "expected_xy_um": target, "commanded_xy_um": command,
                    "measured_xy_um": located,
                    "error_x_um": dx, "error_y_um": dy,
                    "error_distance_um": math.hypot(dx, dy),
                    "commanded_error_x_um": located[0] - command[0],
                    "commanded_error_y_um": located[1] - command[1],
                }
                save_calibration(
                    transform_file, transform, spacing=spacing,
                    calibration_points=report["measurements"][:2],
                    validation=report["validation"])
                save_report(output_dir, report)
            psl.info(f"Located ({row},{col}): X={located[0]:.3f}, Y={located[1]:.3f} um")
            raise_head(psl)
        report["status"] = "complete"
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        save_report(output_dir, report)
    except BaseException as exc:
        report["status"] = "stopped"
        report["error"] = f"{type(exc).__name__}: {exc}"
        save_report(output_dir, report)
        raise

    validation = report["validation"]
    psl.info(
        f"Third-corner error (measured minus predicted): "
        f"dX={validation['error_x_um']:+.3f} um, "
        f"dY={validation['error_y_um']:+.3f} um, "
        f"distance={validation['error_distance_um']:.3f} um. "
        f"Head raised. Results: {output_dir / 'result.json'}")
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
    parser.add_argument("--layout", type=Path, help="Existing layout; omit for folder picker")
    parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe")
    parser.add_argument("--crd-popup-screenshot", type=Path,
                        help="Redefine the CRD popup guard using this screenshot")
    parser.add_argument("--spacing", type=positive_spacing, default=28489.0,
                        help="Plate grid spacing in um (default: 28489)")
    parser.add_argument("--output-dir", type=Path, help="New folder for results and screenshots")
    parser.add_argument("--transform-file", type=Path,
                        default=Path(__file__).resolve().parent / "plate-calibration.json",
                        help="Reusable transform JSON (default: plate-calibration.json beside script)")
    args = parser.parse_args(argv)
    output_dir = (args.output_dir or Path(__file__).resolve().parent / "runs" /
                  ("plate-calibration-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    if (output_dir / "result.json").exists():
        parser.error("Output folder already contains result.json; choose a new folder")
    if (args.transform_file.resolve() == output_dir / "result.json" or
            args.transform_file.suffix.lower() != ".json"):
        parser.error("--transform-file must be a .json file separate from result.json")
    # Check OCR before connecting or moving. Importing the script stays offline.
    reader = TesseractReader(args.tesseract_cmd)
    import playwrightscriptlib as psl

    output_dir.mkdir(parents=True, exist_ok=True)
    psl.logging(True, str(output_dir / "run.log"))
    psl.screenshotOnInfo(True, str(output_dir / "screenshots"))
    psl.alarmOnError()
    connected = False
    try:
        psl.connect(args.url, page_hint="remotedesktop.google.com")
        connected = True
        psl.loadLayout(args.layout)
        psl.checkViewport()
        psl.enableClickGuard("crd-popup", timeout=30,
                             referenceScreenshot=args.crd_popup_screenshot)
        psl.clicksSettleTime(1)
        psl.pauseOnInfo(True)
        run_calibration(psl, tesseract_cmd=reader.executable,
                        output_dir=output_dir, spacing=args.spacing,
                        transform_file=args.transform_file)
        return 0
    finally:
        # Disconnecting does not send a movement, focus or exposure command.
        if connected:
            psl.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
