@echo off
setlocal DisableDelayedExpansion
set "dwl_python=python"
if exist "%~dp0.venv\Scripts\python.exe" set "dwl_python=%~dp0.venv\Scripts\python.exe"
"%dwl_python%" "%~dp0run_folders.py" %*
exit /b %errorlevel%
