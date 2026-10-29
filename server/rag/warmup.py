"""启动时预热嵌入 / 重排模型，避免首问卡在 analyze。

对外暴露 status，供 /health 与连接页展示。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger("ailearning.rag")

# pending | loading | ready | error
_status: dict[str, Any] = {
    "rag_ready": False,
    "rag_status": "pending",
    "rag_detail": "尚未开始加载检索模型",
}


def get_rag_warmup_status() -> dict[str, Any]:
    return dict(_status)


def _set(status: str, detail: str, *, ready: bool = False) -> None:
    _status["rag_status"] = status
    _status["rag_detail"] = detail
    _status["rag_ready"] = ready


def _warm_sync() -> None:
    from server.rag.rag_rerank import _get_cross_encoder
    from server.rag.rag_store import _embedder, get_collection

    try:
        _set("loading", "正在加载嵌入模型（bge-base-en）…")
        _log.info("RAG warmup: loading embedder…")
        _embedder()

        _set("loading", "正在加载重排模型（bge-reranker）…")
        _log.info("RAG warmup: loading reranker…")
        _get_cross_encoder()

        col = get_collection(create=False)
        n = col.count() if col is not None else 0
        _set(
            "ready",
            f"检索模型已就绪（unity_docs ≈ {n} 条）",
            ready=True,
        )
        _log.info("RAG warmup done (unity_docs count=%s)", n)
    except Exception as e:
        _set("error", f"检索模型加载失败: {type(e).__name__}: {e}", ready=False)
        _log.exception("RAG warmup failed")


async def warmup_rag(*, blocking: bool = False) -> None:
    """
    blocking=True：等模型加载完再返回。
    blocking=False：后台加载（启动快；连接页轮询 rag_status）。
    """
    _set("loading", "准备加载检索模型…")
    if blocking:
        await asyncio.to_thread(_warm_sync)
        return
    asyncio.create_task(asyncio.to_thread(_warm_sync))
