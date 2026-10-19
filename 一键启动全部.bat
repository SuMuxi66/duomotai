@echo off
cd /d "%~dp0"
echo === [1/2] main: backend 8010 + frontend 5179 ===
python start_main.py
echo === [2/2] copy frontends 5180-5184 ===
python start_copies.py
echo === All started. Browser: http://localhost:5179 ===
pause
exit /b 0
