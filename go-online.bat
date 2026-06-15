@echo off
REM Puts AutoApply BW online for testers: AI backend + production server + tunnel.
REM The public URL appears in the tunnel window (https://....trycloudflare.com).
REM NOTE: the URL changes each time the tunnel restarts.

start "FreeLLMAPI (AI backend)" cmd /k "cd /d C:\Users\thebe\Documents\freellmapi\server && node dist\index.js"
timeout /t 3 /nobreak >/dev/null
start "AutoApply BW (waitress)" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe run.py --serve"
timeout /t 3 /nobreak >/dev/null
start "Tunnel - public URL appears here" cmd /k "C:\Users\thebe\Documents\tools\cloudflared.exe tunnel --url http://127.0.0.1:8000"
