@echo off
setlocal DisableDelayedExpansion
set "dwl_python=python"
if exist "%~dp0.venv\Scripts\python.exe" set "dwl_python=%~dp0.venv\Scripts\python.exe"
rem The calibration command runs calibrate-stats.py on the file it publishes.
"%dwl_python%" "%~dp0calibrate-plate.py" --latest-run %*
exit /b %errorlevel%
