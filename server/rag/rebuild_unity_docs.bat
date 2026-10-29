@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

echo ========================================
echo   RAG: 全量重做 Unity docs
echo   1) clean   2) build^(重嵌 unity_docs^)
echo ========================================
echo.

set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if exist ".venv\Scripts\python.exe" (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=uv run python
)

echo [1/2] clean...
%PY% -m server.rag.rag_clean %*
if errorlevel 1 (
  echo clean 失败。
  pause
  exit /b 1
)

echo.
echo [2/2] build...
%PY% -m server.rag.rag_build
if errorlevel 1 (
  echo build 失败。
  pause
  exit /b 1
)

echo.
echo 全量完成。
pause
