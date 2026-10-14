@echo off
REM ============================================================
REM  Lesson Sync - test on laptop AND phone (same Wi-Fi)
REM  Starts backend + frontend, prints the URL for your phone.
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- pick the LAN IPv4 address ----
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address" ^| findstr /v /c:"127.0.0.1"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do set LAN_IP=%%b
)
if "%LAN_IP%"=="" set LAN_IP=localhost

echo.
echo   Laptop URL :  http://localhost:5173
echo   Phone URL  :  http://%LAN_IP%:5173
echo   (phone must be on the SAME Wi-Fi; allow Node/Python through
echo    the Windows firewall prompt if it appears)
echo.

REM ---- backend ----
if exist ".venv\Scripts\python.exe" (
  start "Lesson Sync backend" cmd /k "cd backend && ..\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000"
) else (
  start "Lesson Sync backend" cmd /k "cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000"
)

REM ---- frontend (LAN-exposed; vite.config.js already sets host:true) ----
if not exist node_modules (
  echo Installing frontend dependencies...
  call npm install --include=dev
)
start "Lesson Sync frontend" cmd /k "npm run dev"

echo Both servers started in separate windows.
echo Wait a few seconds, then open the URLs above.
endlocal
