@echo off
cd /d "%~dp0"
echo [1/2] pip install backend requirements ...
python -m pip install -r backend\requirements.txt
if errorlevel 1 goto :err
echo [2/2] npm install for 6 frontends ...
for %%d in (frontend frontend-1 frontend-2 frontend-3 frontend-4 frontend-5) do (
  echo   npm install in %%d ...
  cmd /c "cd /d %%d && npm install"
  if errorlevel 1 goto :err
)
echo All dependencies installed.
pause
exit /b 0
:err
echo Install FAILED, see messages above.
pause
exit /b 1
