@echo off
setlocal
cd /d "%~dp0"

rem Windows accepts Path/PATH as one key.  The launcher normalizes its child
rem environment only and verifies /health before returning.
if exist ".venv\Scripts\python.exe" (
    set "PMP_PYTHON=.venv\Scripts\python.exe"
) else (
    set "PMP_PYTHON=py -3.12"
)

echo Starte PolymarketPulse und pruefe /health ...
%PMP_PYTHON% scripts\start_local_server.py --port 8000
if errorlevel 1 (
    echo Start fehlgeschlagen. Details stehen oben; bestehende Prozesse nicht automatisch beendet.
    pause
    exit /b 1
)

start "" "http://127.0.0.1:8000"
echo PolymarketPulse ist bereit. Zum Beenden STOP_POLYMARKETPULSE.bat ausfuehren.
pause
