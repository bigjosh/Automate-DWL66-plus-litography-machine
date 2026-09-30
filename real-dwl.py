# Recorded by playwrightscriptrecord.py on 2026-08-11 18:13
import argparse
from pathlib import Path
from tabnanny import check

import playwrightscriptlib as psl
from xy_readout import TesseractReader, require_xy_position

parser = argparse.ArgumentParser(description="Run the DWL step-and-expose sequence using a saved screen layout.")
parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
parser.add_argument("--layout", type=Path, help="Layout folder; omit to choose a folder interactively")
parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe if not on PATH or in a standard install directory")
parser.add_argument("--crd-popup-screenshot", type=Path,
                    help="Define/redefine the CRD popup reference from a saved full-viewport screenshot")
args = parser.parse_args()

psl.logging(True)
psl.screenshotOnInfo(True)                      # screen before ever info() saved to screenshots/ 
psl.alarmOnError()
psl.info('Checking Tesseract OCR and English language data')
ocr_reader = TesseractReader(args.tesseract_cmd)
psl.info('Connecting to the browser')
psl.connect(args.url, page_hint='remotedesktop.google.com')
psl.loadLayout(args.layout)
psl.checkViewport()
psl.enableClickGuard('crd-popup', timeout=30, referenceScreenshot=args.crd_popup_screenshot)

# just seems safer to give things a little time rather than machine gunning them
psl.clicksSettleTime(1)   # every click/doubleClick now settles for 1s

# shoould we wait for input after each step? 
# comment out the next line to run the script without pausing
psl.pauseOnInfo(True)

# this is the disnace between the centers of the squares in the grid, in um
spacing = 28489

# set to true to actual run an exposure - otherwsie does everything else but.
wetrun=True

# Row 0 is the line of disks closest to the machgine door
# Col 0 is the row of disks closest to the left side of the machine when looking at it from the front

# (row, col) positions to skip
skiplist = [
    (0, 0), 
    (0, 1),
    (0, 2),
    (0, 3),
    (0, 4),
    (0, 5),
    (1, 0),
    (1, 1),
    (1, 2),
    (1, 3),
    (1, 4),
    (1, 5),
    (2, 0),
    (2, 1),
    (2, 2),
    (2, 3),
    # (2, 4),
    # (2, 5),
    # (3, 0),
    # (3, 1),
    # (3, 2),
    # (3, 3),
    # (3, 4),
    # (3, 5),
    # (4, 0),
    # (4 ,1),
    # (4 ,2),
    # (4 ,3),
    # (4 ,4),
    # (4 ,5),
    # (5, 0),
    # (5, 1),
    # (5, 2),
    # (5, 3),
]

for r in range(0, 6):

    for c in range(0, 6):

        if (r, c) in skiplist:
            continue        
    
        psl.info(f"*** Starting cycle for row {r}, column {c} ***")

        xpos = str(int((r - 2.5) * spacing))
        ypos = str(int((c - 2.5) * -spacing))

        psl.info(f"Calculated x position {xpos} and y position {ypos}")  

        # Check z is at 0um
        psl.info("Screen test 'z0um' (matchLevel 0.999) -- Check z is at 0um")
        psl.verifyFrame('z-zero', matchLevel=0.999, message='Screen does not match z0um')

        # Check center button location
        # This really just checks that the DWL software is running and the window is in the expected place
        psl.info("Screen test 'cenetrbutton' (matchLevel 0.99) -- Check center button location")
        psl.verifyFrame('center-button-image', matchLevel=0.99, message='Screen does not match cenetrbutton')

        psl.info("Screen test check z height is zero 'checkz' (matchLevel 0.99)")
        psl.verifyFrame('z-zero-check', matchLevel=0.99, message='Screen does not match checkz')

        # center
        psl.info('Click center-button -- center button')
        psl.click('center-button')
        psl.wait(5)

        # # cleck that we are actually at 0,0 - could be center did not work or someone moved the zero coords and we have to reset
        # psl.info("Screen test check center is at 0,0  (matchLevel 0.99)")
        # psl.verifyFrame('xy-zero-check', matchLevel=0.99, message='Screen does not match checkxy')

        # Require strict X=/Y= integer prefixes within +/-50 um on each axis.
        # here we check that we are at 0,0 after centering
        raw_xy_afetr_center = psl.grabOCRTextLine('actual-xy', tesseract_cmd=ocr_reader.executable)
        actual_x_after_center, actual_y_after_center = require_xy_position(raw_xy_afetr_center, 0, 0)
        psl.info(f'X/Y PASS: OCR={raw_xy_afetr_center!r}; integer readout X={actual_x_after_center}, Y={actual_y_after_center} um; '
                 f'expected X=0, Y=0 um; both integer errors within +/-50 um')


        # execute global alignment
        psl.info('Click global-alignment-tab -- execute global alignment tab')
        psl.click('global-alignment-tab')

        psl.wait(1)

        psl.info("Screen test the X field label is where we want it 'checkx-field-label' (matchLevel 0.99)")
        psl.verifyFrame('checkx-field-label', matchLevel=0.99, message='X field label is wrong')

        # click X
        psl.info('Double click x-field -- click X')
        psl.doubleClick('x-field')

        # xval
        psl.info(f"Send keys {xpos} -- xval")
        psl.sendkeys(xpos)

        psl.info("Screen test the Y field label is where we want it 'checky-field-label' (matchLevel 0.99)")
        psl.verifyFrame('checky-field-label', matchLevel=0.99, message='Y field label is wrong')

        # y
        psl.info('Double click y-field -- y')
        psl.doubleClick('y-field')

        # yval
        psl.info(f"Send keys {ypos} -- yval")
        psl.sendkeys(ypos)

        psl.info("Screen test check move absolute button exists 'checkz' (matchLevel 0.99)")
        psl.verifyFrame('move-absolute-check', matchLevel=0.99, message='Move absolute button does not exist')

        # move absolute
        psl.info('Click move-absolute -- move absolute')
        psl.click('move-absolute')

        # wait for move abs
        psl.info('Wait 5 second(s) -- wait for move abs')
        psl.wait(5) 

        # Require strict X=/Y= integer prefixes within +/-50 um before focus.
        raw_xy = psl.grabOCRTextLine('actual-xy', tesseract_cmd=ocr_reader.executable)
        actual_x, actual_y = require_xy_position(raw_xy, xpos, ypos)
        psl.info(f'X/Y PASS: OCR={raw_xy!r}; integer readout X={actual_x}, Y={actual_y} um; '
                 f'expected X={xpos}, Y={ypos} um; both integer errors within +/-50 um')

        # execute focus
        # if you dont focus before doing optical center, then it will move the stage back to the center to focus
        psl.info('Click focus-button -- focus')
        psl.click('focus-button')

        psl.info('Wait 5 second(s) -- 5')
        psl.wait(5)

        # Check Focus actually started fomr the yellow executing message
        psl.info("Screen test 'focus-executing' (matchLevel 0.99) -- Check Focus executing")
        psl.verifyFrame('focus-executing', matchLevel=0.99, message='Screen does not match focus-executing', delay=10, retrycount=24)

        # Not need, deligated to the optical align script now
        # psl.info('Wait 240 second(s) for focus to complete')
        # psl.wait(240)

        # Check Focus Completed
        psl.info("Screen test 'focusready' (matchLevel 0.99) -- Check Focus Ready")
        psl.verifyFrame('focus-ready', matchLevel=0.99, message='Screen does not match focusready', delay=10, retrycount=24)

        # alignments tab
        psl.info('Click alignment-tab -- alignments tab')
        psl.click('alignment-tab')

        # check for alingments execute button
        psl.info("Screen test 'alingexecutebut' (matchLevel 0.999) -- check for alingments execute button")
        psl.verifyFrame('alignment-execute-image', matchLevel=0.999, message='Screen does not match alingexecutebut')

        # pre-click next to the alignment-execute button to ensure it is focused
        # twice now a chrome remote popup has appeared, stealing focus from the main window
        psl.info('Click NEAR alignment-execute -- execute')
        psl.click('near-execute')

        # execute
        psl.info('Click alignment-execute -- execute')
        psl.click('alignment-execute')

        # align modal
        psl.info('Wait 1 second(s) -- alignment execute modal')
        psl.wait(1)

        # check for the alignment confirm modal
        psl.info("Screen test 'check-alignment-confirm' (matchLevel 0.99) -- check for the alignment confirm modal")
        psl.verifyFrame('check-alignment-confirm', matchLevel=0.99, message='Screen does not match check-alignment-confirm', delay=10, retrycount=24)   


        # preclick newa the align execute button to make sure it is in focus
        psl.info('Click pre click-alignment-confirm -- OK execute find center')
        psl.click('pre-click-alignment-confirm')

        # really execute find center
        psl.info('Click click-alignment-confirm -- OK execute find center')
        psl.click('click-alignment-confirm')


        # let click settle
        psl.info('Wait 1 second(s) -- click align execute button to settle ')
        psl.wait(1)

        # check that the Alignment execute button pushed in  (it has red dot while executing)
        psl.info("Screen test 'alignexecutebut-pushed' (matchLevel 0.999) -- Alignment execute button pushed")
        psl.verifyFrame('alignment-button-pushed', matchLevel=0.999, message='Screen does not match alignexecutebut-pushed', delay=10, retrycount=10)


        # # wait for alignment
        # not needed, now in frameverify
        # # this was too close at 60 so increased to 90
        # psl.info('Wait 100 second(s) -- wait for alignment')
        # psl.wait(100)

        # check that the Alignment execute button unpoped (it has red dot while executing)
        psl.info("Screen test 'alignexecutebut-popped' (matchLevel 0.999) -- Alignment execute button poppped")
        psl.verifyFrame('alignment-button-popped', matchLevel=0.999, message='Screen does not match alignexecutebut-popped', delay=10, retrycount=10)


        # execute focus again after optical center find
        # we were sometimes getting a focus error after the optical center find so we are going to try to focus again
        # the optical center script jerks things around a bit so maybe moves the disk?
        psl.info('Click focus-button -- focus again after optical center find')
        psl.click('focus-button')

        # # 150s was not long enough so bumped up to 240
        # dont included in the verifyframe now
        # psl.info('Wait 240 second(s) for 2nd focus to complete')
        # psl.wait(240)

        # let click settle
        psl.info('Wait 10 second(s) -- click focus button to settle ')
        psl.wait(10)

        # Check Focus Completed
        psl.info("Screen test 'focusready' (matchLevel 0.99) -- Check 2nd Focus Ready")
        psl.verifyFrame('focus-ready', matchLevel=0.99, message='Screen does not match focusready', delay=10, retrycount=24)
        

        # job tab
        psl.info('Click job-tab -- job tab')
        psl.click('job-tab')

        # Job Start button
        psl.info("Screen test 'jobstart' (matchLevel 0.999) -- Job Start button")
        psl.verifyFrame('job-start-image', matchLevel=0.999, message='Screen does not match jobstart')

        if wetrun:
            # start job button
            psl.info('Click job-start -- start job button')
            psl.click('job-start')

            # wait for laser confirm popup
            psl.info('Wait 3 second(s) -- wait for laser confirm popup')
            psl.wait(3)

            # check for laser on box
            psl.info("Screen test 'laseronbox' (matchLevel 0.99) -- check for laser on box")
            psl.verifyFrame('laser-confirm-image', matchLevel=0.998, message='Screen does not match laseronbox')

            # START WRITING
            psl.info('Click laser-confirm -- START WRITING')
            psl.click('laser-confirm')

            # # The actual write job running
            # now in the verifyframe below, so we dont need to wait here
            # psl.info('Wait 780 second(s) -- The actual write job running')
            # psl.wait(780)

            # Job Done Please Remove Tray
            psl.info("Screen test 'jobfinished' (matchLevel 0.99) -- Joob Done Please Remove Tray")
            psl.verifyFrame('job-finished', matchLevel=0.99, message='Screen does not match jobfinished', delay=10, retrycount=78)

            # Job done OK
            psl.info('Click job-done-confirm -- Job done OK')
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
            psl.info('Click laser up button ')
            psl.click('laser-up-click')

            # Check z is at 0um
            psl.info("Screen test 'z0um' (matchLevel 0.999) -- Check z is at 0um")
            psl.verifyFrame('z-zero', matchLevel=0.999, message='Screen does not match z0um', delay=10, retrycount=78)

        # pause
        psl.info('Wait 1 second(s) -- pause')
        psl.wait(1)

psl.info('Script finished')
