# Installation and startup

Setup instructions for the DWL automation scripts and shared Playwright library.
See [README](readme.md) for the project overview and burn-cycle workflow.

## Installation on the local Windows computer

Install these requirements on the computer running Python and Chrome Remote
Desktop, not on the laser writer's control PC. All command blocks below use
**PowerShell**, unless marked otherwise.

### 1. Install Python, Chrome, and Git

- Install **64-bit Python 3.14.7 or a later 3.14 maintenance release** from
  [Python for Windows](https://www.python.org/downloads/windows/). The working
  setup was checked with Python 3.14.7. Include **pip**, **Tcl/Tk and IDLE**, and
  the **Python launcher** when using the full installer. Choose the normal
  installer, not the embeddable distribution.
- Avoid Python 3.14.0 through 3.14.6 for long runs because of the
  [asyncio memory leak fixed in 3.14.7](https://github.com/python/cpython/issues/152569).
- Install Google Chrome. A regular Chrome installation works when launched
  with the debug flags below; a special debug build is not required.
- Install [Git for Windows](https://git-scm.com/downloads/win) if you want to
  clone and update the repositories. Downloading and extracting the repository
  ZIP from GitHub is also sufficient to run the scripts.

Open a new PowerShell window and check Python:

```powershell
py -3.14 --version
```

It should report 3.14.7 or newer on the 3.14 line. If `py` is unavailable, use
the full path to the intended Python executable in the next step, for example
`& "C:\path\to\python.exe" -m venv .venv`. Replace the example path with yours;
PowerShell needs `&` before a quoted executable path.

### 2. Get the DWL scripts and install the Python packages

For a new checkout:

```powershell
git clone https://github.com/bigjosh/Automate-DWL66-plus-litography-machine.git "D:\Github\Automate DWL66-plus litography machine"
```

If you already have this checkout, skip the clone. Change to its folder, then
create a virtual environment and install the requirements:

```powershell
Set-Location "D:\Github\Automate DWL66-plus litography machine"
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use your actual checkout path if it differs. The `.venv` folder is local and
ignored by Git. The commands use its Python directly, so PowerShell activation
and execution-policy changes are unnecessary.

The requirements install:

| Requirement | Purpose |
|---|---|
| `playwright` | Connect to the existing Chrome debug session and send input |
| `Pillow` | Read, crop, and compare screenshots; display image dialogs |
| `opencv-python-headless` | Detect the Chrome Remote Desktop popup before input |
| `numpy` | Image arrays for the popup detector; installed automatically by OpenCV |

Tkinter comes with the Python installation, not pip. Tesseract is installed
separately in step 3. This project attaches to an existing Chrome instance with
[`connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp),
so `playwright install` and its browser downloads are not needed for this workflow.
The Playwright package itself is required; see the
[Playwright Python installation documentation](https://playwright.dev/python/docs/library).

The custom library is already included in this repository as four files:
`playwrightscriptlib.py`, `playwrightscriptlayout.py`, `playwrightscriptocr.py`,
and `playwrightscriptpopup.py`. Keep all four beside `real-dwl.py`. There is no
separate pip installation for `playwrightscriptlib`.

### 3. Install Tesseract OCR and English language data

Install the 64-bit Windows Tesseract 5 package from
[UB Mannheim's installer page](https://github.com/UB-Mannheim/tesseract/wiki),
which is linked by the
[Tesseract installation documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html#windows).
Keep the **English** language data selected (`eng.traineddata`). The scripts
call `tesseract.exe` directly; the Python `pytesseract` wrapper is not required.

The library searches PATH and standard Windows installation directories,
including:

- `C:\Program Files\Tesseract-OCR\tesseract.exe`
- `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`

The second location is used by the current local setup. If you choose another
directory, add it to PATH or supply `--tesseract-cmd` when running the script.
Adding it to PATH is optional when it is found in one of the standard folders.

### 4. Verify installation before connecting to the machine

From the DWL repository folder:

```powershell
.\.venv\Scripts\python.exe -c "import tkinter; from PIL import Image, ImageTk; import cv2, numpy; from playwright.sync_api import sync_playwright; import playwrightscriptlib; print('Python packages OK')"
.\.venv\Scripts\python.exe -c "from playwrightscriptocr import TesseractReader; reader = TesseractReader(); print('Tesseract and English data OK:', reader.executable)"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

These commands do not connect to Chrome or operate the machine. The OCR check
verifies both the executable and English language data. The tests use a fake
browser/machine; a test may be skipped because of the current production skip list.

To check that Tkinter windows work, run the following and close the small demo
window it opens:

```powershell
.\.venv\Scripts\python.exe -m tkinter
```

## Start Chrome with the debug port

Run this in PowerShell:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\crd-data-dir
```

If Chrome is installed under `C:\Program Files (x86)\Google\Chrome\Application`, use
that executable path instead. The `&` is PowerShell's call operator; the old
`start "" ...` command is Command Prompt syntax.

Use port **9222** throughout. The separate `C:\crd-data-dir` profile stores this
debug session's login and settings. Chrome 136 and later require a non-default
profile for remote debugging.
[Chrome's explanation](https://developer.chrome.com/blog/remote-debugging-port)

If Chrome is already using this dedicated profile without debugging enabled,
close that profile's Chrome windows and relaunch with the flags. Verify the
debug endpoint from PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:9222/json/version'
```

Expect a JSON response containing `webSocketDebuggerUrl`. Use `127.0.0.1:9222`
as the script's endpoint. A connection error means the debug instance is not
ready yet; opening an ordinary Chrome window is not sufficient.

In that Chrome instance, log into the Google account with access to the laser
writer, open [Chrome Remote Desktop](https://remotedesktop.google.com/access),
and connect to the machine in the clean room. Keep the computer awake for the
whole run; a full 36-disk cycle can take about 12 hours.

## Prepare the laser writer and screen layout

Before starting the production script:

- Load the disks into the tray and the tray into the machine. Start in the
  LOAD position with the head raised at Z=0.
- Set up the global alignment panel to option P1 for the absolute move.
  In the Stage panel, check **Reset Offset** and **Reset Rotation** beside
  **CENTER** so the next disk's move starts in the calibration coordinate frame.
- Select **Find wafer center optical** for alignment. Set its automatic
  **Set zero** option to **False** so the script can read each optical
  center in stage coordinates before it zeros that disk.
- Use the intended optical autofocus mode and verify the correct exposure job
  is loaded on the Jobs tab.
- Put the laser writer software on the right-hand monitor, maximized, with
  the panels arranged to match the saved references.
- Review the [batch files](#batch-tracking-and-recovery) for the tray being run.
  In `real-dwl.py`, `wetrun=True` enables exposure. With `wetrun=False`, the
  script moves and focuses, uses any newly measured calibration in memory,
  and leaves the disks eligible for exposure.

All four DWL programs automatically load the [layout folder](layout/) beside
the scripts, even when launched from another working directory. It contains the
recorded click positions, OCR rectangle, reference images, and expected browser
viewport size (2542x1455).
On a startup size mismatch, choose **Restore saved size**, **Check again**, or
**Stop the script**. Restore changes the browser size and requires an exact
match before continuing. If a panel moves, restore its position or redefine
the affected entries in `layout/`.

During a comparison with a delay or retries, choose **Final compare now** to
take one fresh comparison immediately and cancel the remaining retries. A
mismatch opens the usual alarm so you can recapture without waiting for the
countdown to finish; a match passes normally. Choosing **Try compare again**
or saving a replacement starts a fresh cycle with the original delay and
retry count.

When a saved image no longer matches, choose **Recapture reference** in the
failed-comparison alarm. The old image is shown before capture and remains
visible while you select the replacement rectangle. Put the application in
the intended expected state, choose **Capture replacement**, select the area,
then review the old and new images and choose **Use as expected reference**.
The named frame's PNG and bounds are saved, retaining the old PNG, and a fresh
comparison runs with the original threshold and retry settings. Cancel returns
to the failed-comparison alarm without saving or passing the check.

Recapturing updates only that frame. Separately redefine click locations and
OCR regions if those controls moved. A ready-state check must still capture
the ready state; do not accept a busy/error screen just to clear a mismatch.
If the script is already running when you update the library, restart it to
load the new button.

### Single-click targets

The first time a DWL program reaches a named single click with no saved
reference, it pauses for you to record one. This includes controls that
already have a saved click coordinate. On the frozen screenshot, select a
tight rectangle around the intended control, mark the click point inside it,
and review the marked preview. Include a distinctive label or icon in the
rectangle. For a click on empty space beside a button, include the adjacent
button so the image identifies the right place. Leave changing coordinates
and status text outside the rectangle.

Approving the capture saves it in `layout/` for all DWL programs. Each
single click requires a similarity score of at least 0.99 on a fresh capture
at those bounds, including the first click after recording. If the control moved, **Show
differences** lets you compare the images while realigning the panel. Then
choose **Try compare again**. Use **Redefine click target** for a deliberate
layout change, or **Stop the script** to end the run. A mismatch blocks the
click in both single-step and continuous mode.

The X/Y entry fields use double clicks because their contents change. They
keep their saved coordinates and the positioning sequence checks the field
labels.

In single-step mode, choose **Show click location** before any named click or
double-click in `real-dwl.py` to see the saved target on a fresh screenshot.
The crosshair and magnified view show where the click will land. Choose
**Back to step** to return to the same pause. Open the preview again to refresh it.

To move the target, choose **Reassign click location**. For a target with a
saved click image, capture the new rectangle and mark the new point inside
it. For a coordinate-only entry, select the new point on the frozen
screenshot. Its first single click will still prompt for a reference.
Choose **Next Step** when ready to send the click.
Canceling the picker leaves the old point unchanged and returns to the pause.
Closing a click-step dialog stops the script. Reassignment sends no input to
the remote application; the pending click still runs its popup guard before
execution. Continuous mode does not show these editing buttons.

The X/Y OCR checks offer **Redefine OCR area**. In single-step mode,
the OCR pause shows the current crop and recognized text. Select **Redefine
OCR area**, drag the replacement rectangle on a frozen screenshot, and review
its crop and OCR result before choosing **Save OCR area**. Allow enough width
for both full coordinates, including minus signs and five-digit values.
The move, center, and zero checks share the same `actual-xy` rectangle.

Production and calibration each try up to five fresh OCR reads. The delay is
five seconds before each capture. After **Set zero**, the first read uses the
five seconds the script already waits for the display to settle.

If all five readings fail, an audible alarm shows the last crop, recognized
text, and reason. **Show capture region** marks the rectangle on the exact
screenshot used for that reading. **Retry** starts another five attempts.
**Redefine OCR area** saves new bounds in the layout. **Change margin** edits
the allowed error in µm for this check only. Production starts each check at
±500 µm (±0.5 mm) per axis.

After editing the rectangle or margin, review the new reading and choose
**Next Step** to validate fresh pixels before proceeding. **STOP** ends the
run. Retrying the OCR does not repeat a move, focus, or exposure.

## Start the production script

For a newly loaded carrier, stop the previous run and execute `.\new-run.bat`
from the DWL repository folder. Check the new folder's run and skip lists.
To start writing, or to resume the same seated carrier, run the following.

```powershell
.\run.bat
```

On a fresh run the two disks farthest apart among the pending entries are
written first. With the default list and an empty skip list these are
`(0,0)` and `(5,5)`. Both must be present in the carrier.

1. For each of the pair, the script restores the stage coordinate frame,
   moves to the nominal position, verifies X/Y, focuses, and runs **Find
   wafer center optical**. It reads the center in stage coordinates, clicks
   **Set zero**, verifies zero, refocuses, and exposes that disk.
2. When both exposures have completed, the script saves the two centers and
   the fitted translation, rotation, and uniform scale in
   `plate-write-calibration.json` and prints the pitch, offset, and rotation.
   This production workflow uses two center finds and has no third-disk error
   measurement.
3. For each remaining pending disk in run-list order, the script computes the
   stage target from the saved transform, verifies the move, and focuses once.
   It clicks **Set zero** at that disk center and verifies zero before opening
   the Job tab and writing.

The script logs the local and calibrated stage targets in µm and rounds the
stage coordinates to whole µm for Move absolute and its OCR check.
If only one disk is pending and the run has no production calibration or
calibration progress, the script optically centers and writes that disk.
One center is insufficient to fit a transform.

If Tesseract is installed in a different location, specify its path with
`--tesseract-cmd`.

```powershell
.\run.bat --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The script starts in single-step mode. Advance with the operator controls or
select continuous operation when ready. Skipping or aborting a required frame
check stops the run. Each invocation appends to `real-dwl.log` in the selected
run folder and saves its screen captures under `screenshots\`.
The script checks the X/Y integer readouts within ±500 µm on each axis after
**CENTER**, after the move, and after **Set zero**. At the next disk it
checks the CENTER reset options again.
If your layout asks for `center-reset-options`, select the two checked boxes.
For `set-zero-button`, capture the Stage panel’s **Set zero** button and mark
a point inside it.
The CRD popup guard waits up to 30 seconds before mouse clicks and keyboard
operations; a persistent popup prevents the pending input and raises an error.

### Batch tracking and recovery

**For a newly loaded carrier**, run `.\new-run.bat` once. It creates
`runs\YYMMDD HHMMSS\` and prints the folder path. Each folder keeps its own
run list, exposure record, and calibration for that carrier placement.

| File in the run folder | How it is used |
| --- | --- |
| `run-list.txt` | Snapshot of the [repository template](run-list.txt). Each `(row,col)` occupies one line. Production follows this order after writing the two calibration disks. Edit the snapshot while the run is stopped to change this carrier's order or subset. Edit the template for future runs. |
| `skip-list.txt` | Starts empty. Production records each disk when it sends the final exposure confirmation. It also saves the calibration pair and its progress as comments for restarts. |
| `plate-write-calibration.json` | The transform used by `run.bat` for this placement. Production saves it after both calibration exposures finish. |
| `plate-calibration.json` | Result of the [optional three-disk survey](#optional-three-disk-calibration). A run containing only the survey and no calibration progress starts its production calibration on the pending disks. |

**To resume the same seated carrier**, run `.\run.bat` again. It selects the
latest `YYMMDD HHMMSS` folder directly under `runs\` by the timestamp in its
name. Editing an older folder's contents or a nested calibration report cannot
change that selection. Folders with other name formats are ignored.

A timestamped folder from the older logging workflow also qualifies. If the
latest folder contains only screenshots or logs, startup stops at that folder.
The program never searches older folders for a usable calibration or skip list.
Use `.\new-run.bat` for a newly loaded carrier. For a partially written carrier,
recover its exposure record or follow the instructions below.

**When upgrading mid-carrier**, keep the carrier seated and use its existing run
and skip lists. If they already live in the latest timestamped run, launch
`.\run.bat` as usual from the repository. A previous three-disk survey
can stay in place.

For an older carrier whose lists live beside `real-dwl.py`, run this command
from the repository folder to use its existing exposure record. Creating a new
run for this carrier would start with an empty skip list and could expose disks
twice.

```powershell
.\.venv\Scripts\python.exe real-dwl.py --run-list ".\run-list.txt" --skip-list ".\skip-list.txt" --transform-file ".\plate-write-calibration.json"
```

For a carrier started with the old hardcoded skip list, enter all previously
started disks and intentional skips in its skip list before resuming.

**Job Start** opens the laser confirmation. The final confirmation starts
writing, and the script records that disk immediately afterward, before it
waits for completion. A disk whose exposure was interrupted therefore stays
in the skip list.

Stopping at the confirmation button’s target-image check leaves the disk
eligible to run. If the script stops during the actual confirmation click,
the skip list can contain
a line such as `# pending exposure (2,3)`. The next launch stops on that line.
Check the machine's job history for that disk. If exposure started, replace
the entire line with `(2,3)`. If it definitely did not start, remove the line.
Keep the rest of the file and resume.

Keep the `# auto-calibration` comments in the skip list. They preserve the
selected disks, measured centers, and recorded completions across restarts.
If a calibration disk started writing but its completion was never recorded,
the script asks you to check the machine's job history. Choose **I verified
exposure completed** only after confirming the disk finished and the machine
is ready. Choose **Stop** if completion is uncertain.

## Optional three-disk calibration

Use `.\calibrate.bat` for a separate survey when you want an independent
third-disk error measurement. It writes `plate-calibration.json`. The
production launcher uses its own `plate-write-calibration.json`.

Start with the head raised at Z=0. Select **Find wafer center optical**, turn
off its automatic **Set zero**, and check **Reset Offset** and **Reset Rotation**
beside **CENTER**. Keep the carrier seated and the same stage coordinate frame
for calibration and production. Calibration moves, focuses, measures three
optical centers, and raises the head. It sends no exposure commands.

```powershell
.\calibrate.bat
```

The script starts in single-step mode and retains the click reassignment,
reference recapture, OCR-area editing, and CRD popup guard. Check the
`laser-up-click` location when first using the head-raise step if that point
has not yet been updated for the new screen layout. Every required frame
check must pass. **Skip and continue** or **Abort verify** stops calibration.

1. The script selects the two farthest-apart disks in the calibration run list.
   It measures them at their nominal grid locations and fits uniform scale,
   rotation, and translation from their optical centers.
2. It selects a third disk with the greatest perpendicular distance from the line
   through the first pair. Ties follow run-list order. On the default tray the
   fit uses `(0,0)` and `(5,5)`, and the check uses `(0,5)`. Choose at least
   three noncollinear disks that are physically present. Calibration uses the
   full supplied run list, including disks already in the exposure skip list.
3. The script reloads its candidate transform and moves to the predicted third
   center. It measures that disk independently and compares its actual optical
   center with the unrounded prediction. **A straight-line error of 500 µm or
   less passes**, including exactly 500 µm. The head is raised after the
   measurement. A failed check ends calibration and prevents publication.

On success, `plate-calibration.json` contains the two fit measurements, the
transform, and the independent measurement and error. The launcher then prints
the nominal and measured pitch, stage X/Y offsets, rotation in degrees, scale
and third-disk result. To print survey statistics again, pass the path to that
run's `plate-calibration.json` to `python calibrate-stats.py`. Running
`python calibrate-stats.py` with no arguments displays the latest run's
production calibration.

If the exposure run list contains fewer than three disks or lies on one line,
supply a separate list of mounted disks with `.\calibrate.bat --run-list "path\to\calibration-list.txt"`.
This changes the disks used for calibration. The resulting transform still
belongs to the newest run.

For any nominal plate center `(x, y)` in micrometers, the saved mapping is
`machine_xy = scale * rotation_matrix * [x, y] + translation_um`.
Equivalently, multiply `transform_matrix` by the column vector `[x, y, 1]`.
In Python, `spacing, transform = load_validated_plate_calibration(path)`
loads the validated mapping. Use `transform.apply(plate_position(row, col, spacing))`
for a disk's stage center. Only the final machine command is rounded.

Use `--spacing` for another nominal pitch in µm or `--tesseract-cmd` for a
different OCR executable. Each calibration attempt creates a
`plate-calibration-<timestamp>` subfolder inside the selected run for its
report, raw OCR strings, log, and screenshots. When remeasuring a seated
carrier, calibration archives the previous active transform in that attempt's
folder. It saves results after each center find and publishes a passing
`plate-calibration.json` in the selected run. Use `--output-dir` to choose a
different directory for the calibration report and diagnostics.

Calibration reads preserve decimal fractions and use the strict whole-line
parser. Before each focus, the default allowed error is ±50 µm on each axis.
For the two fit centers, the default limit is a radial distance strictly less
than half the grid spacing from the command. Larger shifts can indicate a
wrong disk or coordinate reset. The independent 500 µm acceptance limit is
fixed and applies after the third center is read.
On interruption, no automatic recovery motion is sent; inspect the current
head/stage state before restarting.

## Updating the shared library (optional)

A DWL checkout already contains the library. To deliberately update it from
the shared source repository, first clone that repository if you do not have it:

```powershell
git clone https://github.com/bigjosh/playwright-script-recorder.git "D:\Github\Playwritght-Script"
```

If it already exists, update that checkout as needed. With the DWL repository
as your current folder, copy **all four modules from the same library version**:

```powershell
$libraryDir = 'D:\Github\Playwritght-Script'
Copy-Item -LiteralPath "$libraryDir\playwrightscriptlib.py", "$libraryDir\playwrightscriptlayout.py", "$libraryDir\playwrightscriptocr.py", "$libraryDir\playwrightscriptpopup.py" -Destination .
.\.venv\Scripts\python.exe -m pip install -r "$libraryDir\requirements.txt"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

`Playwritght-Script` is the spelling of the existing local folder. Adjust the
path if yours differs. Copying only `playwrightscriptlib.py` is insufficient:
it imports the other three modules. The separate `xy_readout.py` is part of
this DWL repository and must remain beside `real-dwl.py`.

## Installation troubleshooting

| Error | What to check |
|---|---|
| `No module named playwright`, `PIL`, `cv2`, or `numpy` | Run pip through the same `.venv\Scripts\python.exe` used to start the script, with `-m pip install -r requirements.txt`. |
| Missing `playwrightscriptlayout`, `playwrightscriptocr`, or `playwrightscriptpopup` | Restore/copy all four shared-library modules together. |
| Missing `tkinter` or `_tkinter` | Modify the Python installation to include Tcl/Tk; then recreate the environment if needed. |
| Tesseract not found | Install the engine, then use a standard directory, PATH, or `--tesseract-cmd`. |
| English data missing | Install `eng.traineddata` for the selected Tesseract installation. |
| Cannot connect to `127.0.0.1:9222` | Launch Chrome with both debug flags and verify `/json/version`. |
