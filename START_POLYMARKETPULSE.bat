@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtuelle Umgebung nicht gefunden unter .venv\Scripts\python.exe
    echo Bitte zuerst gemaess README.md einrichten: py -3.12 -m venv .venv, dann pip install -e ".[dev]"
    pause
    exit /b 1
)

echo Pruefe Port 8000...
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Port 8000 wird bereits von Prozess %%p verwendet.
    echo Falls das eine vorherige PolymarketPulse-Instanz ist, einfach den Browser oeffnen:
    echo   http://127.0.0.1:8000
    echo Andernfalls zuerst STOP_POLYMARKETPULSE.bat ausfuehren oder den Prozess beenden.
    start "" "http://127.0.0.1:8000"
    pause
    exit /b 0
)

echo Starte PolymarketPulse ...
start "PolymarketPulse Server" /min ".venv\Scripts\python.exe" -m polymarketpulse.cli serve

echo Warte auf den Server...
timeout /t 3 /nobreak >nul

start "" "http://127.0.0.1:8000"

echo.
echo PolymarketPulse laeuft im Hintergrund (minimiertes Fenster "PolymarketPulse Server").
echo Zum Beenden: STOP_POLYMARKETPULSE.bat ausfuehren oder das Server-Fenster schliessen.
pause
