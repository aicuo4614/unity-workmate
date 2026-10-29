@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

echo ========================================
echo   RAG: Unity docs HTML -^> clean Markdown
echo   extracted/ -^> clean/  (不改 extracted)
echo ========================================
echo.

set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m server.rag.rag_clean %*
) else (
  uv run python -m server.rag.rag_clean %*
)

if errorlevel 1 (
  echo.
  echo clean 失败。
  pause
  exit /b 1
)
echo.
echo 完成。可接着跑 build_unity_docs.bat 嵌入。
pause
