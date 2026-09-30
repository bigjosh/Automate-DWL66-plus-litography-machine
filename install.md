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
- Set up the global alignment panel to option P1 and select
  `Find wafer center optical` in Alignments.
- Use the intended optical autofocus mode and verify the correct exposure job
  is loaded on the Jobs tab.
- Put the laser writer software on the right-hand monitor, maximized, with
  the panels arranged to match the selected layout.
- Check `skiplist` and `wetrun` in `real-dwl.py`. `wetrun=True` enables exposure;
  `wetrun=False` still performs machine movement and focusing.

The `layouts\2026-09-11-afternoon` folder contains the recorded click positions,
OCR rectangle, reference images, and expected browser viewport size (2542x1455).
On a startup size mismatch, choose **Restore saved size**, **Check again**, or
**Stop the script**. Restore changes the browser size and requires an exact
match before continuing. Changes to the actual panel arrangement require
redefining the affected entries or choosing a new layout folder.

## Start the production script

From the DWL repository folder, after installation and machine preparation:

```powershell
.\.venv\Scripts\python.exe real-dwl.py --layout "layouts\2026-09-11-afternoon"
```

Omit `--layout` to choose a folder interactively. If Tesseract is installed in a
different location, use an explicit path, for example:

```powershell
.\.venv\Scripts\python.exe real-dwl.py --layout "layouts\2026-09-11-afternoon" --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The script starts in single-step mode. Advance with the operator controls or
select continuous operation when ready. Logs and screenshots are saved under
`runs\<timestamp>`. The script checks the X/Y integer readouts within +/-50 um
on each axis after centering and after the absolute move, before focusing.
The CRD popup guard waits up to 30 seconds before mouse clicks and keyboard
operations; a persistent popup prevents the pending input and raises an error.

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
