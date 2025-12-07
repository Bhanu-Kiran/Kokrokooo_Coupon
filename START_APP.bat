@echo off
setlocal enabledelayedexpansion

REM -------------------------
REM  START_APP.bat  —  Safe Windows-only version (no tee)
REM -------------------------

set "PROJ_DIR=%~dp0"
cd /d "%PROJ_DIR%"

set "LOG=%PROJ_DIR%installer_log.txt"

echo ====================================================== > "%LOG%"
echo Start time: %DATE% %TIME% >> "%LOG%"
echo Project dir: %PROJ_DIR% >> "%LOG%"
echo ====================================================== >> "%LOG%"

echo Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. >> "%LOG%"
    echo Python not found. Please install Python 3.x.
    pause
    exit /b 1
)

set "VENV_DIR=%PROJ_DIR%.venv"

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo Using existing virtual environment. >> "%LOG%"
) else (
    echo Creating virtual environment...
    python -m venv "%VENV_DIR%" >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo ERROR: Failed to create virtualenv. >> "%LOG%"
        echo Failed to create virtualenv. Check installer_log.txt.
        pause
        exit /b 2
    )
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

if not exist "%VENV_PY%" (
    echo ERROR: Virtualenv Python missing. >> "%LOG%"
    echo Virtualenv Python missing. Something went wrong.
    pause
    exit /b 3
)

echo Upgrading pip... >> "%LOG%"
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1

if exist "%PROJ_DIR%requirements.txt" (
    echo Installing requirements... >> "%LOG%"
    "%VENV_PIP%" install -r "%PROJ_DIR%requirements.txt" >> "%LOG%" 2>&1
)

echo Starting server...
start "CouponApp Server" cmd /k ""%VENV_PY%" "%PROJ_DIR%run.py%""


echo Waiting for server to start... >> "%LOG%"
timeout /t 3 >nul

echo Opening http://127.0.0.1:5000
start "" "http://127.0.0.1:5000"

echo ====================================================== >> "%LOG%"
echo Completed at %DATE% %TIME% >> "%LOG%"
echo ====================================================== >> "%LOG%"

echo Launch complete. See installer_log.txt for details.
pause
endlocal
