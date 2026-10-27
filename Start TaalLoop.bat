@echo off
rem Double-click to start TaalLoop on Windows.
rem First run: creates a private Python environment in .venv and installs the
rem app (a few minutes). Later runs start straight away.
setlocal
cd /d "%~dp0"
set "PORT=8000"
title TaalLoop

curl -s -o nul "http://localhost:%PORT%/api/config" >nul 2>nul
if not errorlevel 1 (
  echo TaalLoop is already running - opening it in your browser.
  start "" "http://localhost:%PORT%"
  exit /b 0
)

if exist ".venv\Scripts\python.exe" goto install

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto makevenv
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto makevenv

echo TaalLoop needs Python 3.11 or newer. Opening the download page...
echo When installing, tick "Add python.exe to PATH" on the first screen.
echo Then double-click this file again.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:makevenv
echo First run: setting up TaalLoop. This takes a few minutes...
%PY% -m venv .venv
if errorlevel 1 goto failed

:install
rem Reinstall only when the requirements changed (first run, or after an update).
copy /b requirements.txt + requirements-demucs.txt "%TEMP%\taalloop-req.txt" >nul
fc /b "%TEMP%\taalloop-req.txt" ".venv\installed-requirements.txt" >nul 2>nul
if not errorlevel 1 goto run
echo Installing TaalLoop's components (about 1 GB to download)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-demucs.txt
if errorlevel 1 goto failed
copy /y "%TEMP%\taalloop-req.txt" ".venv\installed-requirements.txt" >nul

:run
set "LAN_IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq 'Dhcp' } | Select-Object -First 1).IPAddress"`) do set "LAN_IP=%%i"
echo.
echo TaalLoop is starting...
echo   On this PC:                 http://localhost:%PORT%
if defined LAN_IP echo   On your iPad (same Wi-Fi):  http://%LAN_IP%:%PORT%
echo.
echo Keep this window open while you use TaalLoop. Close it to stop TaalLoop.
echo If Windows Firewall asks, allow access on private networks (for the iPad).
echo.
start "" /b powershell -NoProfile -Command "Start-Sleep 3; Start-Process 'http://localhost:%PORT%'"
".venv\Scripts\python.exe" -m uvicorn taalloop.main:app --host 0.0.0.0 --port %PORT%
pause
exit /b 0

:failed
echo.
echo The installation failed - see the messages above.
pause
exit /b 1
