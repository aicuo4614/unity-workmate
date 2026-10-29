@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

echo ========================================
echo   RAG: 重建 Unity docs 向量索引
echo   读 clean/unity-2022.3 -^> chroma collection unity_docs
echo   （会清空并重嵌 unity_docs，其它 collection 不动）
echo ========================================
echo.

set PYTHONPATH=%CD%
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m server.rag.rag_build %*
) else (
  uv run python -m server.rag.rag_build %*
)

if errorlevel 1 (
  echo.
  echo build 失败。
  pause
  exit /b 1
)
echo.
echo 索引完成。
pause
