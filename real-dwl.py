# Recorded by playwrightscriptrecord.py on 2026-08-11 18:13
import argparse
import time
from pathlib import Path

import playwrightscriptlib as psl
from batch_state import BatchState
from plate_transform import load_plate_calibration, plate_position
from xy_readout import POSITION_TOLERANCE_UM, TesseractReader, parse_xy_integers, require_xy_position

parser = argparse.ArgumentParser(description="Run the DWL step-and-expose sequence using a saved screen layout.")
parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
parser.add_argument("--layout", type=Path, help="Layout folder; omit to choose a folder interactively")
parser.add_argument("--run-list", type=Path, default=Path(__file__).resolve().with_name("run-list.txt"),
                    help="Ordered disk positions (default: run-list.txt beside this script)")
parser.add_argument("--skip-list", type=Path, default=Path(__file__).resolve().with_name("skip-list.txt"),
                    help="Batch exposure record (default: skip-list.txt beside this script)")
parser.add_argument("--transform-file", type=Path,
                    default=Path(__file__).resolve().with_name("plate-calibration.json"),
                    help="Plate calibration JSON (default: plate-calibration.json beside this script)")
parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe if not on PATH or in a standard install directory")
parser.add_argument("--crd-popup-screenshot", type=Path,
                    help="Define/redefine the CRD popup reference from a saved full-viewport screenshot")
args = parser.parse_args()

psl.logging(True)
psl.screenshotOnInfo(True)                      # screen before ever info() saved to screenshots/ 
psl.alarmOnError()
CLICK_SETTLE_SECONDS = 1

# set to true to actual run an exposure - otherwsie does everything else but.
wetrun=True

def require_frame(name, **options):
    if not psl.verifyFrame(name, **options):
        raise RuntimeError(f"Required check {name!r} did not pass. Stopping this run.")


def check_stage_position(expected_x, expected_y, description="X/Y PASS", *, initial_delay=None):
    text = psl.grabOCRTextLine(
        'actual-xy', tesseract_cmd=ocr_reader.executable, review=True,
        retrycount=5, delay=5, initial_delay=initial_delay, margin=POSITION_TOLERANCE_UM,
        validator=lambda text, margin: require_xy_position(
            text, expected_x, expected_y, tolerance=margin))
    actual_x, actual_y = parse_xy_integers(text)
    psl.info(f'{description}: OCR={text!r}; integer readout X={actual_x}, Y={actual_y} um; '
             f'expected X={expected_x}, Y={expected_y} um')


with BatchState(args.run_list, args.skip_list) as batch:
    psl.info(f"Batch has {len(batch.pending)} disk(s) left to start.")
    if batch.pending:
        psl.info(f"Loading plate calibration from {args.transform_file.resolve()}")
        spacing, plate_to_stage = load_plate_calibration(args.transform_file)
        psl.info('Checking Tesseract OCR and English language data')
        ocr_reader = TesseractReader(args.tesseract_cmd)
        psl.info('Connecting to the browser')
        psl.connect(args.url, page_hint='remotedesktop.google.com')
        psl.loadLayout(args.layout)
        psl.checkViewport()
        psl.enableClickGuard('crd-popup', timeout=30, referenceScreenshot=args.crd_popup_screenshot)
        psl.clicksSettleTime(CLICK_SETTLE_SECONDS)
        psl.pauseOnInfo(True)

    # Row 0 is nearest the machine door. Column 0 is on the left when facing the machine.
    for r, c in batch.pending:
        psl.info(f"*** Starting cycle for row {r}, column {c} ***")

        local_x, local_y = plate_position(r, c, spacing)
        psl.info(f"Local target ({r},{c}): X={local_x}, Y={local_y} um")
        stage_x, stage_y = plate_to_stage.apply((local_x, local_y))
        xpos, ypos = str(round(stage_x)), str(round(stage_y))
        psl.info(f"Stage target ({r},{c}): X={stage_x:.3f}, Y={stage_y:.3f} um; "
                 f"Move absolute command X={xpos}, Y={ypos} um")

        # Check z is at 0um
        psl.info("Screen test 'z0um' (matchLevel 0.999) -- Check z is at 0um")
        psl.verifyFrame('z-zero', matchLevel=0.999, message='Screen does not match z0um')

        # Check center button location
        # This really just checks that the DWL software is running and the window is in the expected place
        psl.info("Screen test 'cenetrbutton' (matchLevel 0.99) -- Check center button location")
        psl.verifyFrame('center-button-image', matchLevel=0.99, message='Screen does not match cenetrbutton')

        psl.info("Screen test check z height is zero 'checkz' (matchLevel 0.99)")
        psl.verifyFrame('z-zero-check', matchLevel=0.99, message='Screen does not match checkz')

        # CENTER restores the machine coordinates used by the calibration when
        # both reset options are selected. Set zero changes the job origin.
        psl.info('Verify Reset Offset and Reset Rotation are checked before CENTER')
        require_frame('center-reset-options', matchLevel=1.0,
                      message='Expected both CENTER reset options to be checked')
        psl.info('Click center-button -- center button', point='center-button')
        psl.click('center-button')
        psl.wait(5)

        # # cleck that we are actually at 0,0 - could be center did not work or someone moved the zero coords and we have to reset
        # psl.info("Screen test check center is at 0,0  (matchLevel 0.99)")
        # psl.verifyFrame('xy-zero-check', matchLevel=0.99, message='Screen does not match checkxy')

        check_stage_position(0, 0)


        # execute global alignment
        psl.info('Click global-alignment-tab -- execute global alignment tab', point='global-alignment-tab')
        psl.click('global-alignment-tab')

        psl.wait(1)

        psl.info("Screen test the X field label is where we want it 'checkx-field-label' (matchLevel 0.99)")
        psl.verifyFrame('checkx-field-label', matchLevel=0.99, message='X field label is wrong')

        # click X
        psl.info('Double click x-field -- click X', point='x-field')
        psl.doubleClick('x-field')

        # xval
        psl.info(f"Send keys {xpos} -- xval")
        psl.sendkeys(xpos)

        psl.info("Screen test the Y field label is where we want it 'checky-field-label' (matchLevel 0.99)")
        psl.verifyFrame('checky-field-label', matchLevel=0.99, message='Y field label is wrong')

        # y
        psl.info('Double click y-field -- y', point='y-field')
        psl.doubleClick('y-field')

        # yval
        psl.info(f"Send keys {ypos} -- yval")
        psl.sendkeys(ypos)

        psl.info("Screen test check move absolute button exists 'checkz' (matchLevel 0.99)")
        psl.verifyFrame('move-absolute-check', matchLevel=0.99, message='Move absolute button does not exist')

        # move absolute
        psl.info('Click move-absolute -- move absolute', point='move-absolute')
        psl.click('move-absolute')

        # wait for move abs
        psl.info('Wait 5 second(s) -- wait for move abs')
        psl.wait(5) 

        # Verify the calibrated stage command before focusing.
        check_stage_position(xpos, ypos)

        psl.info('Focus at the verified disk center', point='focus-button')
        psl.click('focus-button')
        psl.wait(5)
        psl.info('Wait for focus to start')
        require_frame('focus-executing', matchLevel=0.96, delay=10, retrycount=24)
        psl.info('Wait for focus to be ready')
        require_frame('focus-ready', matchLevel=0.96, delay=10, retrycount=24)

        psl.info('Verify the Stage Set zero button')
        require_frame('set-zero-button-image', matchLevel=0.99)
        psl.info('Set the job origin at this disk center', point='set-zero-button')
        psl.click('set-zero-button')
        # Give the X/Y display five seconds to update, including the click's settle time.
        time.sleep(max(0, 5 - CLICK_SETTLE_SECONDS))
        check_stage_position(0, 0, 'Disk origin verified after Set zero', initial_delay=0)

        # job tab
        psl.info('Click job-tab -- job tab', point='job-tab')
        psl.click('job-tab')

        # Job Start button
        psl.info("Screen test 'jobstart' (matchLevel 0.999) -- Job Start button")
        psl.verifyFrame('job-start-image', matchLevel=0.999, message='Screen does not match jobstart')

        if wetrun:
            # start job button
            psl.info('Click job-start -- start job button', point='job-start')
            psl.click('job-start')

            # wait for laser confirm popup
            psl.info('Wait 3 second(s) -- wait for laser confirm popup')
            psl.wait(3)

            # check for laser on box
            psl.info("Screen test 'laseronbox' (matchLevel 0.99) -- check for laser on box")
            psl.verifyFrame('laser-confirm-image', matchLevel=0.998, message='Screen does not match laseronbox')

            # START WRITING
            psl.info('Click laser-confirm -- START WRITING', point='laser-confirm')
            with batch.starting_exposure((r, c)):
                psl.click('laser-confirm')

            # # The actual write job running
            # now in the verifyframe below, so we dont need to wait here
            # psl.info('Wait 780 second(s) -- The actual write job running')
            # psl.wait(780)

            # Job Done Please Remove Tray
            psl.info("Screen test 'jobfinished' (matchLevel 0.99) -- Joob Done Please Remove Tray")
            psl.verifyFrame('job-finished', matchLevel=0.99, message='Screen does not match jobfinished', delay=10, retrycount=78)

            # Job done OK
            psl.info('Click job-done-confirm -- Job done OK', point='job-done-confirm')
            psl.click('job-done-confirm')

            # pause
            psl.info('Wait 1 second(s) -- pause')
            psl.wait(1)

            # Below did not work on first try?
            # Process finished OK
            # psl.info('Click at (1820, 821) -- Process finished OK')
            # psl.click(1820, 821)

            # This does not work becuase the finished modal opens in a different place every time
            # # process finished modal ok button
            # psl.info("Screen test 'finishedok' (matchLevel 0.99) -- process finished modal ok button")
            # psl.verifyFrame('capture-immediate-finishedok-1827,848,1878,869.png', (1827, 848, 1878, 869), 0.99, 'Screen does not match finishedok')

            # # click ok
            # psl.info('Click at (1853, 858) -- click ok on process finished modal')
            # psl.click(1853, 858)

            # Check stage ready after write completes- hopefully this will indicate that the write is complete
            psl.info("Screen test 'stageready' (matchLevel 0.99) -- Check stage ready after write completes")
            psl.verifyFrame('stage-ready', matchLevel=0.99, message='Screen does not match stageready')

            # ok now we press space bar and hope that the modal is in focus so we ket the OK button
            # I cant think of any other way to clean this moving modal. 
            psl.info("Press space bar to hopefully clear job complete modal")
            psl.sendkeys(" ")
        else:
            psl.info("Skipping write sequence as 'wetrun' is False")
            # Job done OK
            psl.info('Click laser up button ', point='laser-up-click')
            psl.click('laser-up-click')

            # Check z is at 0um
            psl.info("Screen test 'z0um' (matchLevel 0.999) -- Check z is at 0um")
            psl.verifyFrame('z-zero', matchLevel=0.999, message='Screen does not match z0um', delay=10, retrycount=78)

        # pause
        psl.info('Wait 1 second(s) -- pause')
        psl.wait(1)

psl.info('Script finished')
