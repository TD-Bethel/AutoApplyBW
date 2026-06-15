@echo off
REM Apply a code update with minimal disruption.
REM Restarts ONLY the app server - the tunnel (public URL) and the AI backend
REM keep running, the database is untouched, and users stay logged in.
REM Expect a ~3 second blip; anyone mid-click just refreshes.

echo Stopping app server...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >/dev/null 2>&1
timeout /t 2 /nobreak >/dev/null

echo Starting updated app server...
start "AutoApply BW (waitress)" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe run.py --serve"
timeout /t 4 /nobreak >/dev/null
echo.
echo Update applied. Same public URL, users still logged in.
