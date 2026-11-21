"""Run a DWL disk batch, calibrating from two pending disks when needed."""

import argparse
from pathlib import Path
import time

import playwrightscriptlib as psl
from batch_calibration import BatchCalibration
from batch_state import BatchState
from dwl_actions import (
    LAYOUT_DIR, check_frame, click, focus, optical_center_find, read_xy, send_absolute_move,
)
from plate_transform import load_plate_calibration, plate_position
from xy_readout import POSITION_TOLERANCE_UM, TesseractReader, parse_xy_integers, require_xy_position


# Set False to exercise the positioning and focus sequence without exposing disks.
wetrun = True
CLICK_SETTLE_SECONDS = 1


def parse_args():
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run the DWL step-and-expose sequence.")
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
    parser.add_argument("--run-list", type=Path, default=folder / "run-list.txt",
                        help="Ordered disk positions (default: run-list.txt beside this script)")
    parser.add_argument("--skip-list", type=Path, default=folder / "skip-list.txt",
                        help="Batch exposure record (default: skip-list.txt beside this script)")
    parser.add_argument("--transform-file", type=Path, default=folder / "plate-calibration.json",
                        help="Load this plate calibration, or create it from pending disks if missing")
    parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe if needed")
    parser.add_argument("--crd-popup-screenshot", type=Path,
                        help="Define the CRD popup reference from a saved full-viewport screenshot")
    return parser.parse_args()


def connect_to_machine(args):
    psl.info("Checking Tesseract OCR and English language data")
    ocr_cmd = TesseractReader(args.tesseract_cmd).executable
    psl.info("Connecting to the browser")
    psl.connect(args.url, page_hint="remotedesktop.google.com")
    psl.loadLayout(LAYOUT_DIR)
    psl.checkViewport()
    psl.enableClickGuard("crd-popup", timeout=30, referenceScreenshot=args.crd_popup_screenshot)
    psl.clicksSettleTime(CLICK_SETTLE_SECONDS)
    psl.pauseOnInfo(True)
    return ocr_cmd


def check_stage_position(expected, ocr_cmd, description="X/Y PASS", *, initial_delay=None):
    x, y = expected
    text = psl.grabOCRTextLine(
        "actual-xy", tesseract_cmd=ocr_cmd, review=True, retrycount=5, delay=5,
        initial_delay=initial_delay, margin=POSITION_TOLERANCE_UM,
        validator=lambda text, margin: require_xy_position(text, x, y, tolerance=margin))
    actual_x, actual_y = parse_xy_integers(text)
    psl.info(f"{description}: OCR={text!r}; integer readout X={actual_x}, Y={actual_y} um; "
             f"expected X={x}, Y={y} um")


def restore_stage_frame(ocr_cmd, *, calibrating):
    # CENTER with both resets restores the frame in which the two centers are measured.
    check_frame(psl, "z-zero", 0.999, required=calibrating)
    check_frame(psl, "z-zero-check", 0.99, required=calibrating)
    check_frame(psl, "center-reset-options", 1.0,
                message="Expected both CENTER reset options to be checked")
    click(psl, "center-button")
    psl.wait(5)
    check_stage_position((0, 0), ocr_cmd)


def measure_disk_center(calibration, position, command, ocr_cmd):
    optical_center_find(psl)
    text, center = read_xy(psl, ocr_cmd, center_near=command, max_center_shift=calibration.spacing / 2)
    calibration.record_center(position, text, center)
    psl.info(f"Located disk {position} at stage X={center[0]}, Y={center[1]} um")
    # An optical center find can leave the machine unfocused.
    focus(psl, "Refocus at the optically located disk center")


def set_disk_origin(ocr_cmd):
    click(psl, "set-zero-button", "Set the job origin at this disk center")
    time.sleep(max(0, 5 - CLICK_SETTLE_SECONDS))
    check_stage_position((0, 0), ocr_cmd, "Disk origin verified after Set zero", initial_delay=0)


def expose_disk(batch, position, *, calibrating):
    click(psl, "job-start")
    psl.wait(3)
    check_frame(psl, "laser-confirm-image", 0.998, required=calibrating)
    psl.info("Click laser-confirm -- START WRITING", point="laser-confirm")
    psl.click("laser-confirm", dispatch_context=batch.starting_exposure(position))
    check_frame(psl, "job-finished", 0.99, delay=10, retrycount=78, required=calibrating)


def dismiss_finished_job(*, calibrating):
    click(psl, "job-done-confirm")
    psl.wait(1)
    check_frame(psl, "stage-ready", 0.99, required=calibrating)
    # This final modal moves between runs. Space activates the focused OK button.
    psl.info("Press space bar to clear the process-finished modal")
    psl.sendkeys(" ")


def run_disk(batch, position, spacing, transform, ocr_cmd, *, calibration=None):
    calibrating = calibration is not None
    row, col = position
    psl.info(f"*** Starting cycle for row {row}, column {col} ***")
    local = plate_position(row, col, spacing)
    psl.info(f"Local target ({row},{col}): X={local[0]}, Y={local[1]} um")
    stage = local if calibrating else transform.apply(local)
    command = tuple(round(value) for value in stage)
    psl.info(f"Stage target ({row},{col}): X={stage[0]:.3f}, Y={stage[1]:.3f} um; "
             f"Move absolute command X={command[0]}, Y={command[1]} um")

    restore_stage_frame(ocr_cmd, calibrating=calibrating)
    send_absolute_move(psl, command, require_match=calibrating)
    check_stage_position(command, ocr_cmd)
    focus(psl, "Focus at the verified stage position")
    if calibrating:
        measure_disk_center(calibration, position, command, ocr_cmd)
    set_disk_origin(ocr_cmd)
    click(psl, "job-tab")
    check_frame(psl, "job-start-image", 0.999, required=calibrating)

    if wetrun:
        expose_disk(batch, position, calibrating=calibrating)
    else:
        psl.info("Dry run: raising the head without starting an exposure")
        click(psl, "laser-up-click")
        check_frame(psl, "z-zero", 0.999, delay=10, retrycount=78, required=calibrating)
    if calibrating:
        calibration.finish_anchor(position)
    if wetrun:
        dismiss_finished_job(calibrating=calibrating)
    psl.wait(1)


def confirm_finished(position):
    return psl.alarm(
        f"The exposure for calibration disk {position} was started, but its completion "
        "was not recorded. Check the machine's job history for this disk and confirm "
        "that the exposure finished and the machine is ready to continue.",
        ("I verified exposure completed", "Stop")) == 0


def run_batch(args, batch):
    psl.info(f"Batch has {len(batch.pending)} disk(s) left to start.")
    automatic = None
    if args.transform_file.exists():
        psl.info(f"Loading plate calibration from {args.transform_file.resolve()}")
        spacing, transform = load_plate_calibration(args.transform_file)
        BatchCalibration.acknowledge_saved(batch, args.transform_file, dry_run=not wetrun)
        positions = batch.pending
    else:
        automatic = BatchCalibration(batch, args.transform_file, dry_run=not wetrun)
        automatic.resume(confirm_finished)
        positions = automatic.positions
        spacing, transform = automatic.spacing, automatic.transform

    if not positions:
        return
    ocr_cmd = connect_to_machine(args)
    if automatic and transform is None:
        psl.info(f"Automatic calibration using disk(s) {automatic.anchors}. "
                 "Use Find wafer center optical with its Set zero option off.")
    for position in positions:
        anchor_plan = automatic if automatic and position in automatic.anchors else None
        run_disk(batch, position, spacing, transform, ocr_cmd, calibration=anchor_plan)
        if anchor_plan and anchor_plan.transform is not None:
            transform = anchor_plan.transform
            location = str(args.transform_file) if wetrun else "memory (dry run)"
            psl.info(f"Calibration ready in {location}: scale={transform.scale:.8f}, "
                     f"rotation={transform.angle_degrees:.6f} degrees, "
                     f"translation X={transform.tx:.3f}, Y={transform.ty:.3f} um")


def main():
    args = parse_args()
    psl.logging(True)
    psl.screenshotOnInfo(True)
    psl.alarmOnError()
    with BatchState(args.run_list, args.skip_list) as batch:
        run_batch(args, batch)
    psl.info("Script finished")


if __name__ == "__main__":
    main()
