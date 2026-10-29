@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Unity Assistant - Web
echo   http://127.0.0.1:5173
echo   Start server\run.bat first
echo ========================================
echo.

if not exist "node_modules\" (
  echo First run: npm install ...
  call npm install
  if errorlevel 1 (
    echo npm install failed. Check Node.js / network.
    pause
    exit /b 1
  )
)

REM 旧 Vite 常占着 5173；启动前清掉本机该端口的 LISTENING 进程
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":5173 .*LISTENING"') do (
  echo Port 5173 in use by PID %%p — stopping it ...
  taskkill /F /PID %%p >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting Vite on 127.0.0.1:5173 ...
echo Open http://127.0.0.1:5173 when ready.
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5173/"

call npm run dev -- --host 127.0.0.1 --port 5173
if errorlevel 1 (
  echo.
  echo Client exited with an error. If port in use, close the old window and retry.
  pause
)
