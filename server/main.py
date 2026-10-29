"""FastAPI 入口。BLAS 线程限制必须放在其它 import 之前。"""
from __future__ import annotations

import os

# 须在 numpy/OpenBLAS 被间接加载前设置。否则内存紧时 worker 卡在 :8000 Bound，前端扫设备 502。
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from contextlib import asynccontextmanager
from pathlib import Path

import fastapi
from fastapi.staticfiles import StaticFiles

from server import tools
from server.graph import graph_manager
from server.infra.app_logging import get_logger

_log = get_logger("ailearning")


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    from server.infra.app_logging import setup_app_logging
    from server.infra.checkpoint_manager import close_checkpoint
    from server.rag.warmup import warmup_rag

    setup_app_logging()
    # 单步失败也要进入 yield：adb 扫设备不依赖 Unity MCP / RAG
    try:
        await tools.init_tools()
    except Exception:
        _log.exception("init_tools 失败，API 仍会启动（真机扫描可用）")
    try:
        await graph_manager.init_graph()
    except Exception:
        _log.exception("init_graph 失败，对话图不可用")
    try:
        # 后台预热 bge；失败只影响文档检索，不挡 /mcp/android/*
        await warmup_rag(blocking=False)
    except Exception:
        _log.exception("warmup_rag 调度失败")
    yield
    try:
        await tools.close_tools()
    except Exception:
        _log.exception("close_tools 失败")
    try:
        await close_checkpoint()
    except Exception:
        _log.exception("close_checkpoint 失败")


app = fastapi.FastAPI(lifespan=lifespan)
from server import listeners  # noqa: E402, F401
from server.model_api import router as model_router
app.include_router(model_router)
from server.renderdoc_api import router as renderdoc_router
app.include_router(renderdoc_router)

# 可选：静态前端（避免挂 / 抢 API；开发请用 vite :5173）
_web_dist = Path(__file__).resolve().parents[1] / "client-web" / "dist"
if _web_dist.is_dir():
    app.mount("/app", StaticFiles(directory=str(_web_dist), html=True), name="client-web")
