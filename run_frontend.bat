@echo off
REM Start the Lesson Sync frontend (Vite dev server) on http://localhost:5173
cd /d "%~dp0"
if not exist node_modules (
  echo Installing frontend dependencies...
  call npm install
)
call npm run dev
