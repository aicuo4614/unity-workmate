@echo off
cd /d "%~dp0.."

echo ========================================
echo   Unity Assistant - Server
echo   http://127.0.0.1:8000
echo ========================================
echo.

REM Prefer venv python (avoid "uv run" reinstalling locked .pyd on Windows).
REM When deps change, run at repo root: uv sync --link-mode=copy
if not exist ".venv\Scripts\python.exe" (
  echo .venv missing, running uv sync ...
  uv sync --link-mode=copy
  if errorlevel 1 (
    echo uv sync failed.
    pause
    exit /b 1
  )
)

REM Stuck uvicorn often holds :8000 as Bound, not LISTENING.
REM Cleanup lives in kill_port.ps1 so cmd quoting stays intact.
echo Cleaning leftover uvicorn on :8000 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill_port.ps1"
timeout /t 1 /nobreak >nul

REM Limit BLAS threads so a low-RAM machine does not freeze the worker.
set OPENBLAS_NUM_THREADS=1
set OMP_NUM_THREADS=1
set MKL_NUM_THREADS=1
set VECLIB_MAXIMUM_THREADS=1
set TOKENIZERS_PARALLELISM=false

echo Starting uvicorn ...
".venv\Scripts\python.exe" -m uvicorn server.main:app --reload --reload-dir server --reload-dir common --host 127.0.0.1 --port 8000
if errorlevel 1 (
  echo.
  echo Server exited with an error.
  echo If port stuck / OpenBLAS OOM: close this window, free RAM, run server\run.bat again.
  echo If multidict access denied: kill leftover python.exe, then retry.
  pause
)
