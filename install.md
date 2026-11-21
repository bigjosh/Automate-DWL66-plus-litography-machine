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

- Load the 36 disks into the tray and the tray into the machine; start with
  the machine in its LOAD position.
- Set up the global alignment panel to option P1 for the absolute move.
  In the Stage panel, check **Reset Offset** and **Reset Rotation** beside
  **CENTER** so the next disk's move starts in the calibration coordinate frame.
- Use the intended optical autofocus mode and verify the correct exposure job
  is loaded on the Jobs tab.
- Put the laser writer software on the right-hand monitor, maximized, with
  the panels arranged to match the selected layout.
- Review the [batch files](#batch-tracking-and-recovery) for the tray being run.
  In `real-dwl.py`, `wetrun=True` enables exposure. With `wetrun=False`, the
  script still moves and focuses, and the disks remain eligible for exposure.

The `layouts\2026-09-11-afternoon` folder contains the recorded click positions,
OCR rectangle, reference images, and expected browser viewport size (2542x1455).
On a startup size mismatch, choose **Restore saved size**, **Check again**, or
**Stop the script**. Restore changes the browser size and requires an exact
match before continuing. Changes to the actual panel arrangement require
redefining the affected entries or choosing a new layout folder.

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

In single-step mode, choose **Show click location** before any named click or
double-click in `real-dwl.py` to see the saved target on a fresh screenshot.
The crosshair and magnified view show where the click will land. Choose
**Back to step** to return to the same pause. Open the preview again to refresh it.

To move the target, choose **Reassign click location** and click the new
target on the frozen screenshot. The point is saved in the selected layout.
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
All three checks share `actual-xy`, so one saved change applies to all three.

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

For a newly loaded carrier, stop the previous run and execute `.\new-batch.bat`
from the DWL repository folder. When resuming with the same carrier still
seated, go directly to the production command.

```powershell
.\.venv\Scripts\python.exe real-dwl.py --layout "layouts\2026-09-11-afternoon"
```

At startup the script uses `plate-calibration.json` beside `real-dwl.py`, or
the file specified by `--transform-file`. A missing file triggers automatic
calibration. Each move logs the local and stage targets in µm and rounds the
stage coordinates to whole µm for Move absolute and its OCR check.

Omit `--layout` to choose a folder interactively. If Tesseract is installed in a
different location, use an explicit path, for example:

```powershell
.\.venv\Scripts\python.exe real-dwl.py --layout "layouts\2026-09-11-afternoon" --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The script starts in single-step mode. Advance with the operator controls or
select continuous operation when ready. Logs and screenshots are saved under
`runs\<timestamp>`. The script checks the X/Y integer readouts within ±500 µm
on each axis after CENTER and after the move. For a calibrated disk it focuses
once, waits for the executing and ready indicators, clicks **Set zero**, and
checks that Actual X/Y are within the same tolerance of (0,0) before opening
the Job tab. At the next disk it checks the CENTER reset options again.
If your layout asks for `center-reset-options`, select the two checked boxes.
For `set-zero-button-image` and `set-zero-button`, select the Stage panel's
**Set zero** button as the reference image and click point, respectively.
The CRD popup guard waits up to 30 seconds before mouse clicks and keyboard
operations; a persistent popup prevents the pending input and raises an error.

### Automatic plate calibration

Removing and reseating the carrier changes its position relative to the stage.
Every newly loaded batch needs a fresh calibration for that placement.

Select **Find wafer center optical** with its automatic **Set zero** option
off. When the selected calibration file is missing, the script chooses the
two farthest-apart pending disks. Equal distances are resolved in run-list
order. For a full tray in the default order, the pair is `(0,0)` and `(5,5)`.
The nominal moves use the tray's 28,489 µm pitch.

For each of these disks the script restores the common stage frame with
**CENTER**, verifies the nominal move, focuses, and runs the optical center
find. It saves the measured stage X/Y with decimal precision, refocuses and
waits for focus-ready, then sets and verifies the job origin before exposure.
After both exposures have passed the job-finished check, it saves and reloads
the two-point transform. The remaining disks use calibrated moves and one
focus each. A single remaining disk is optically centered and exposed without
creating a two-point calibration. Dry runs keep measurements and the fitted
transform in memory.

Keep the calibration progress comments in `skip-list.txt` when resuming.
If the script stopped after an exposure started but before it recorded
completion for a calibration disk, it asks you to verify completion in the
machine's job history. Choose **I verified exposure completed** only once
you have confirmed that disk finished and the machine is ready. **Stop**
leaves it pending review. Resuming uses the saved center and skips the
already-started exposure. If an exposure *start* itself is uncertain, follow
the pending-line instructions below first.

### Batch tracking and recovery

The default [run-list.txt](run-list.txt) and local `skip-list.txt` live beside
`real-dwl.py`. Each position is written on its own line as `(row,col)`. The
run list ships with all 36 disks in row order. Edit it while the script is
stopped to change the order or select a subset. Use `--run-list` and
`--skip-list` to supply alternative file paths.

For a **new batch**, stop the previous run and execute `.\new-batch.bat`.
It deletes `skip-list.txt` and `plate-calibration.json` beside the batch
file. The next production run uses `run-list.txt` to select the disks for
fresh calibration and exposure.

If you override `--skip-list` or `--transform-file`, select fresh paths for
the new batch or clear the corresponding files before launch. The reset
command uses the default filenames.

To **resume the same seated carrier**, keep the batch files and run the
previous production command again. The script skips every listed position.
The earlier hardcoded list omitted row 0. If you are finishing an existing tray, copy any
already-started disks and any positions you still intend to skip into the
skip list before the first launch.

**Job Start** opens the laser confirmation. The final confirmation starts
writing, and the script records that disk immediately afterward, before it
waits for completion. A disk whose exposure was interrupted therefore stays
in the skip list.

If the script stops during the confirmation click, the skip list can contain
a line such as `# pending exposure (2,3)`. The next launch stops on that line.
Check the machine's job history for that disk. If exposure started, replace
the entire line with `(2,3)`. If it definitely did not start, remove the line.
Keep the rest of the file and resume.

## Test a plate calibration with three corners

Use the existing layout and select `Find wafer center optical`. Disable the
setting that zeros X/Y after center finding, and keep the stage origin and
rotation unchanged throughout this test. Start with the head raised at Z=0
and the usual nominal plate coordinate system. The test moves and focuses,
but does not expose or change the production script.

```powershell
.\.venv\Scripts\python.exe calibrate-plate.py --layout "layouts\2026-09-11-afternoon"
```

The script starts in single-step mode and retains the click reassignment,
reference recapture, OCR-area editing, and CRD popup guard. Check the
`laser-up-click` location when first using the head-raise step if that point
has not yet been updated for the new screen layout. Every required frame
check must pass; **Skip and continue** or **Abort verify** stops this test.

It visits `(0,0)` and `(5,5)` at their nominal positions, focuses once on each,
finds each center, and records the displayed coordinates. It fits translation,
rotation, and uniform scale, allowing the printed jig's actual pitch to differ
from the nominal 28,489 um spacing. The nominal half-micrometer coordinates
are retained for the fit; movement commands are
rounded to whole micrometers. The head is raised and verified between disks.

After fitting, it writes `plate-calibration.json` beside the script, then
**reloads that file** to predict and move to `(0,5)`. A final focus and center
find measures the independent error. The third corner never enters the fit.
The script reports signed X/Y errors and their combined distance, measured
minus predicted, and raises the head at completion. It also reports the
measured-to-nominal baseline ratio used as the scale factor. Both calibration
points fit exactly by construction; that is not an independent accuracy check.

The reusable JSON contains a version, units, nominal grid spacing, scale,
rotation matrix, translation, a complete 3x3 transform matrix, both calibration
measurements, and the third-corner validation result. New files use version 2
and model `similarity_2d`. The file has `validation: null` until that measurement
succeeds. The
`plate_transform.load_calibration()` helper validates the file and loads its
transform; it also reads the original version-1 rigid files with scale fixed
at 1. The version-2 loader rejects missing/invalid scale and inconsistent
matrices. Production seeks use the plate placement and stage coordinate frame
in which the centers were measured. Reloading/reseating the plate or changing
that coordinate frame requires a new calibration. Two reference points determine a common scale,
rotation, and translation; they do not determine separate X/Y stretch, shear,
or individual disk placement errors. The third corner checks the model.

For any nominal plate center `(x, y)` in micrometers, the saved mapping is
`machine_xy = scale * rotation_matrix * [x, y] + translation_um`.
Equivalently, multiply `transform_matrix` by the column vector `[x, y, 1]`.
`load_calibration(path).apply(plate_position(row, col))` performs the complete
mapping with the default nominal pitch. Keep fractional nominal centers;
round only the final machine command. This maps disk centers and does not
change the size or orientation of the exposure design itself.

Use `--transform-file "path\plate-calibration.json"` to choose another shared
file, `--spacing` for another pitch in um, or `--tesseract-cmd` if needed.
Existing calibration files are backed up into the new run folder before
replacement. Detailed results, raw OCR strings, log, and screenshots are
saved under `runs\plate-calibration-<timestamp>`. Results are saved after
each center find; `--output-dir` selects a different run folder.

Calibration reads preserve decimal fractions and use the strict whole-line
parser. Before each focus, the default allowed error is ±50 µm on each axis.
For a found center, the default limit is a radial distance strictly less than
half the grid spacing from its command. Larger shifts can indicate a wrong
disk or coordinate reset. The OCR alarm identifies which check failed.
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
