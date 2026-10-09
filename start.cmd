@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python3 >nul 2>nul
        if not errorlevel 1 (
            python3 -m venv .venv
        ) else (
            python -m venv .venv
        )
    )
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" IoT.py
goto finished
:failed
echo.
echo Setup failed. Install Python 3.10 or newer and check the error above.
:finished
pause
