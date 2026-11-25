@echo off
REM Launch PX Py Builder with a console attached.
REM
REM run_builder.bat uses pythonw, which has no console -- so if the builder
REM fails to start, the traceback goes nowhere. Use this one to see it.

setlocal
set "ROOT=%~dp0"
pushd "%ROOT%"

set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Launching PX Py Builder with %PY%
echo.
"%PY%" "%ROOT%run_builder.py"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo Builder exited with code %RC%.
pause

popd
endlocal
exit /b %RC%
