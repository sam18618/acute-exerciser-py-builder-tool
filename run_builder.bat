@echo off
REM Launch PX Py Builder.
REM
REM Uses the project virtual environment when it is there, and falls back to
REM whatever Python is on PATH -- the builder only needs the standard library,
REM so it runs fine without the venv.

setlocal
set "ROOT=%~dp0"
pushd "%ROOT%"

set "VENV_PY=%ROOT%.venv\Scripts\pythonw.exe"

if exist "%VENV_PY%" (
    start "" "%VENV_PY%" "%ROOT%run_builder.py"
    goto :done
)

where pythonw.exe >nul 2>&1
if %ERRORLEVEL%==0 (
    echo No virtual environment found, using pythonw from PATH.
    start "" pythonw.exe "%ROOT%run_builder.py"
    goto :done
)

where py.exe >nul 2>&1
if %ERRORLEVEL%==0 (
    echo No virtual environment found, using the py launcher.
    start "" py.exe -3 "%ROOT%run_builder.py"
    goto :done
)

echo.
echo Could not find Python.
echo Install Python 3.10 or newer, or create the project venv with:
echo     py -3 -m venv .venv
echo.
pause
popd
endlocal
exit /b 1

:done
popd
endlocal
exit /b 0
