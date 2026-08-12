# Recorded by playwrightscriptrecord.py on 2026-08-11 18:13
import sys

import playwrightscriptlib as psl

psl.logging(True)
psl.alarmOnError()
psl.info('Connecting to the browser')
psl.connect(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:9222', page_hint='remotedesktop.google.com')
psl.checkViewport(2560, 1463)

# just seems safer to give things a little time rather than machine gunning them
psl.clicksSettleTime(1)   # every click/doubleClick now settles for 1s

# shoould we wait for input after each step? 
# comment out the next line to run the script without pausing
psl.pauseOnInfo(True)

# this is the disnace between the centers of the squares in the grid, in um
spacing = 28489

# Row 0 is left column if you are looking at the stage from the front, row 5 is right column.
for r in range(0, 6):

    for c in range(0, 6):
    
        psl.info(f"*** Starting cycle for row {r}, column {c} ***")

        xpos = str(int((r - 2.5) * spacing))
        ypos = str(int((c - 2.5) * -spacing))

        psl.info(f"Calculated x position {xpos} and y position {ypos}")   


        # Check center button location
        # This really just checks that the DWL software is running and the window is in the expected place
        psl.info("Screen test 'cenetrbutton' (matchLevel 0.99) -- Check center button location")
        psl.verifyFrame('capture-immediate-cenetrbutton-2288,728,2338,761.png', (2288, 728, 2338, 761), 0.99, 'Screen does not match cenetrbutton')

        psl.info("Screen test check z height is zero 'checkz' (matchLevel 0.99)")
        psl.verifyFrame('capture-immediate-checkz-2221,796,2258,809.png', (2221, 796, 2258, 809), 0.99, 'Screen does not match checkz')

        # center
        psl.info('Click at (2312, 742) -- center')
        psl.click(2312, 742)
        psl.wait(2)

        # execute global alignment
        psl.info('Click at (1417, 466) -- execute global alignment')
        psl.click(1417, 466)

        psl.wait(1)

        # click X
        psl.info('Double click at (1393, 868) -- click X')
        psl.doubleClick(1393, 868)

        # xval
        psl.info(f"Send keys {xpos} -- xval")
        psl.sendkeys(xpos)

        # y
        psl.info('Double click at (1454, 883) -- y')
        psl.doubleClick(1454, 883)

        # yval
        psl.info(f"Send keys {ypos} -- yval")
        psl.sendkeys(ypos)

        # move absolute
        psl.info('Click at (1707, 936) -- move absolute')
        psl.click(1707, 936)

        # wait for move abs
        psl.info('Wait 2 second(s) -- wait for move abs')
        psl.wait(2)

        # execute focus
        psl.info('Click at (2262, 869) -- focus')
        psl.click(2262, 869)

        # # 150
        # psl.info('Wait 150 second(s) -- 150')
        # psl.wait(150)

        # 150s was not long enough so bumped up to 240
        psl.info('Wait 240 second(s) for focus to complete')
        psl.wait(240)


        # Check Focus Completed
        psl.info("Screen test 'focusready' (matchLevel 0.99) -- Check Focus Ready")
        psl.verifyFrame('capture-immediate-focusready-2384,779,2413,791.png', (2384, 779, 2413, 791), 0.99, 'Screen does not match focusready')

        # alignments tab
        psl.info('Click at (1344, 467) -- alignments tab')
        psl.click(1344, 467)

        # execute
        psl.info('Click at (2156, 538) -- execute')
        psl.click(2156, 538)

        # align modal
        psl.info('Wait 1 second(s) -- align modal')
        psl.wait(1)

        # really execute find center
        psl.info('Click at (1953, 796) -- really execute find center')
        psl.click(1953, 796)

        # wait for alignment
        # this was too close at 60 so increased to 90
        psl.info('Wait 90 second(s) -- wait for alignment')
        psl.wait(90)

        # job tab
        psl.info('Click at (1294, 464) -- job tab')
        psl.click(1294, 464)

        # start job button
        psl.info('Click at (1529, 484) -- start job button')
        psl.click(1529, 484)

        # wait for laser confirm popup
        psl.info('Wait 3 second(s) -- wait for laser confirm popup')
        psl.wait(3)

        # check for laser on box
        psl.info("Screen test 'laseronbox' (matchLevel 0.99) -- check for laser on box")
        psl.verifyFrame('capture-real-dwl-laseronbox-1793,716,2058,808.png', (1793, 716, 2058, 808), 0.99, 'Screen does not match laseronbox')

        # START WRITING
        psl.info('Click at (1914, 797) -- START WRITING')
        psl.click(1914, 797)

        # The actual write job running
        psl.info('Wait 780 second(s) -- The actual write job running')
        psl.wait(780)

        # job done popup
        psl.info("Screen test 'jobdonepop' (matchLevel 0.99) -- job done popup")
        psl.verifyFrame('capture-real-dwl-jobdonepop-1796,710,2053,807.png', (1796, 710, 2053, 807), 0.99, 'Screen does not match jobdonepop')

        # Job done OK
        psl.info('Click at (1970, 797) -- Job done OK')
        psl.click(1970, 797)

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
        psl.verifyFrame('capture-immediate-stageready-2220,540,2251,558.png', (2220, 540, 2251, 558), 0.99, 'Screen does not match stageready')

        # ok now we press space bar and hope that the modal is in focus so we ket the OK button
        # I cant think of any other way to clean this moving modal. 
        psl.info("Press space bar to hopefully clear job complete modal")
        psl.sendkeys(" ")

        # pause
        psl.info('Wait 1 second(s) -- pause')
        psl.wait(1)

psl.info('Script finished')
