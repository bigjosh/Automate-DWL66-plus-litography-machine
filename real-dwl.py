"""Optically center and expose two pending disks, then write from their calibrated positions."""

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import time

import playwrightscriptlib as psl
from batch_calibration import BatchCalibration
from batch_state import BatchState
from dwl_actions import (
    CLICK_SETTLE_SECONDS, check_frame, check_stage_position, click, configure_machine_screen,
    focus, optical_center_find, raise_head, read_xy, restore_stage_frame, send_absolute_move,
)
from plate_transform import load_validated_plate_calibration, plate_position
from run_folders import add_run_arguments, run_session
from xy_readout import TesseractReader


# Set False to exercise the positioning and focus sequence without exposing disks.
wetrun = True


def parse_args():
    parser = argparse.ArgumentParser(description="Run the DWL step-and-expose sequence.")
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
    add_run_arguments(parser)
    parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe if needed")
    parser.add_argument("--crd-popup-screenshot", type=Path,
                        help="Define the CRD popup reference from a saved full-viewport screenshot")
    return parser.parse_args()


def connect_to_machine(args):
    psl.info("Checking Tesseract OCR and English language data")
    ocr_cmd = TesseractReader(args.tesseract_cmd).executable
    psl.info("Connecting to the browser")
    psl.connect(args.url, page_hint="remotedesktop.google.com")
    configure_machine_screen(psl, args.crd_popup_screenshot)
    return ocr_cmd


def set_disk_origin(ocr_cmd):
    click(psl, "set-zero-button", "Set the job origin at this disk center")
    time.sleep(max(0, 5 - CLICK_SETTLE_SECONDS))
    check_stage_position(psl, (0, 0), ocr_cmd, "Disk origin verified after Set zero", initial_delay=0)


def expose_disk(batch, position):
    click(psl, "job-start")
    psl.wait(3)
    check_frame(psl, "laser-confirm-image", 0.998)
    psl.info("Click laser-confirm -- START WRITING", point="laser-confirm")
    psl.click("laser-confirm", dispatch_context=batch.starting_exposure(position))
    check_frame(psl, "job-finished", 0.99, delay=10, retrycount=78)


def dismiss_finished_job():
    click(psl, "job-done-confirm")
    psl.wait(1)
    check_frame(psl, "stage-ready", 0.99)
    # This final modal moves between runs. Space activates the focused OK button.
    psl.info("Press space bar to clear the process-finished modal")
    psl.sendkeys(" ")


def run_disk(batch, position, spacing, transform, ocr_cmd, *, anchor_plan=None):
    row, col = position
    psl.info(f"*** Starting cycle for row {row}, column {col} ***")
    local = plate_position(row, col, spacing)
    psl.info(f"Local target ({row},{col}): X={local[0]}, Y={local[1]} um")
    stage = local if anchor_plan is not None else transform.apply(local)
    command = tuple(round(value) for value in stage)
    psl.info(f"Stage target ({row},{col}): X={stage[0]:.3f}, Y={stage[1]:.3f} um; "
             f"Move absolute command X={command[0]}, Y={command[1]} um")

    restore_stage_frame(psl, ocr_cmd)
    send_absolute_move(psl, command)
    check_stage_position(psl, command, ocr_cmd)
    focus(psl, "Focus at the verified stage position")
    if anchor_plan is not None:
        optical_center_find(psl)
        text, center = read_xy(psl, ocr_cmd, center_near=command, max_center_shift=spacing / 2)
        anchor_plan.record_center(position, text, center)
        psl.info(f"Located disk {position} at stage X={center[0]}, Y={center[1]} um")
    set_disk_origin(ocr_cmd)
    if anchor_plan is not None:
        focus(psl, "Refocus at the optically located disk center")
    click(psl, "job-tab")
    check_frame(psl, "job-start-image", 0.999)

    if wetrun:
        expose_disk(batch, position)
    else:
        psl.info("Dry run: raising the head without starting an exposure")
        raise_head(psl, "Raise the head after the dry run")
    if anchor_plan is not None:
        anchor_plan.finish_anchor(position)
    if wetrun:
        dismiss_finished_job()
    psl.wait(1)


def confirm_finished(position):
    return psl.alarm(
        f"The exposure for calibration disk {position} started. Its completion was not recorded. "
        "Check the machine's job history and confirm that this disk finished and the machine is ready.",
        ("I verified exposure completed", "Stop")) == 0


def print_calibration_stats(path):
    subprocess.run([sys.executable, str(Path(__file__).with_name("calibrate-stats.py")),
                    str(path)], check=True)


def run_batch(args, batch):
    psl.info(f"Batch has {len(batch.pending)} disk(s) left to start.")
    if not batch.pending and batch.calibration_checkpoint() is None:
        return
    path = args.transform_file
    plan = None
    if path.exists():
        psl.info(f"Loading plate calibration from {path.resolve()}")
        spacing, transform = load_validated_plate_calibration(path)
        BatchCalibration.acknowledge_saved(batch, path, dry_run=not wetrun)
        positions = batch.pending
    else:
        plan = BatchCalibration(batch, path, dry_run=not wetrun)
        plan.resume(confirm_finished)
        spacing, transform = plan.spacing, plan.transform
        positions = plan.positions
        if transform is not None and wetrun:
            print_calibration_stats(path)
        elif positions:
            psl.info(f"Optical calibration disk(s) {plan.anchors}. "
                     "Find wafer center optical must have its Set zero option off.")
    if not positions:
        return
    ocr_cmd = connect_to_machine(args)
    for position in positions:
        anchor = plan if plan is not None and position in plan.anchors else None
        run_disk(batch, position, spacing, transform, ocr_cmd, anchor_plan=anchor)
        if anchor is not None and anchor.transform is not None:
            transform = anchor.transform
            if wetrun:
                print_calibration_stats(path)


def main():
    args = parse_args()
    project = Path(__file__).resolve().parent
    with run_session(project, args) as run_dir:
        output = run_dir or project / "runs" / "legacy" / datetime.now().strftime("%y%m%d %H%M%S-%f")
        psl.setRunFolder(output)
        psl.logging(True)
        psl.screenshotOnInfo(True, str(output / "screenshots"))
        psl.alarmOnError()
        with BatchState(args.run_list, args.skip_list, create_skip=run_dir is None) as batch:
            run_batch(args, batch)
        psl.info("Script finished")


if __name__ == "__main__":
    main()
