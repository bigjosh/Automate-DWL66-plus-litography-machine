@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not "%~1"=="" (
    echo Usage: new-batch.bat ^(resets the default files beside this script^) 1>&2
    exit /b 2
)

set "DWL_BATCH_DIR=%~dp0"
rem Calibration and production both hold the skip list open while running.
rem Delete the calibration first so a failed deletion preserves the old exposure record.
powershell.exe -NoProfile -NonInteractive -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$skipPath = Join-Path $env:DWL_BATCH_DIR 'skip-list.txt';" ^
    "$calibrationPath = Join-Path $env:DWL_BATCH_DIR 'plate-calibration.json';" ^
    "try {" ^
    "    $handle = [IO.File]::Open($skipPath, 'OpenOrCreate', 'ReadWrite', 'Delete');" ^
    "    try {" ^
    "        [IO.File]::Delete($calibrationPath);" ^
    "        [IO.File]::Delete($skipPath);" ^
    "    } finally { $handle.Dispose() }" ^
    "} catch { [Console]::Error.WriteLine('Batch reset failed: ' + $_.Exception.GetBaseException().Message); exit 1 }"
if errorlevel 1 exit /b 1

echo New batch ready. Run calibrate.bat for this carrier, then run.bat to expose.
exit /b 0
