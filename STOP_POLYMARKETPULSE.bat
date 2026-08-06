@echo off
setlocal

echo Suche laufenden PolymarketPulse-Server (Port 8000)...
set FOUND=0
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Beende Prozess %%p ...
    taskkill /PID %%p /F >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="1" (
    echo PolymarketPulse wurde beendet.
) else (
    echo Kein laufender PolymarketPulse-Server auf Port 8000 gefunden.
)
pause
