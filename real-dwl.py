"""Expose pending disks using the carrier's independently verified stage calibration."""

import argparse
from pathlib import Path
import time

import playwrightscriptlib as psl
from batch_state import BatchState
from dwl_actions import (
    CLICK_SETTLE_SECONDS, check_frame, check_stage_position, click, configure_machine_screen,
    focus, raise_head, restore_stage_frame, send_absolute_move,
)
from plate_transform import load_validated_plate_calibration, plate_position
from xy_readout import TesseractReader


# Set False to exercise the positioning and focus sequence without exposing disks.
wetrun = True


def parse_args():
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run the DWL step-and-expose sequence.")
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
    parser.add_argument("--run-list", type=Path, default=folder / "run-list.txt",
                        help="Ordered disk positions (default: run-list.txt beside this script)")
    parser.add_argument("--skip-list", type=Path, default=folder / "skip-list.txt",
                        help="Batch exposure record (default: skip-list.txt beside this script)")
    parser.add_argument("--transform-file", type=Path, default=folder / "plate-calibration.json",
                        help="Verified plate calibration produced by calibrate.bat")
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


def run_disk(batch, position, spacing, transform, ocr_cmd):
    row, col = position
    psl.info(f"*** Starting cycle for row {row}, column {col} ***")
    local = plate_position(row, col, spacing)
    psl.info(f"Local target ({row},{col}): X={local[0]}, Y={local[1]} um")
    stage = transform.apply(local)
    command = tuple(round(value) for value in stage)
    psl.info(f"Stage target ({row},{col}): X={stage[0]:.3f}, Y={stage[1]:.3f} um; "
             f"Move absolute command X={command[0]}, Y={command[1]} um")

    restore_stage_frame(psl, ocr_cmd)
    send_absolute_move(psl, command)
    check_stage_position(psl, command, ocr_cmd)
    focus(psl, "Focus at the verified stage position")
    set_disk_origin(ocr_cmd)
    click(psl, "job-tab")
    check_frame(psl, "job-start-image", 0.999)

    if wetrun:
        expose_disk(batch, position)
        dismiss_finished_job()
    else:
        psl.info("Dry run: raising the head without starting an exposure")
        raise_head(psl, "Raise the head after the dry run")
    psl.wait(1)


def run_batch(args, batch):
    psl.info(f"Batch has {len(batch.pending)} disk(s) left to start.")
    if not batch.pending:
        return
    psl.info(f"Loading plate calibration from {args.transform_file.resolve()}")
    spacing, transform = load_validated_plate_calibration(args.transform_file)
    ocr_cmd = connect_to_machine(args)
    for position in batch.pending:
        run_disk(batch, position, spacing, transform, ocr_cmd)


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
