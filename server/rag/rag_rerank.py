"""交叉编码器重排：对向量召回候选按 query-doc 相关性精排。

默认用仓库内 `server/knowledge/models/bge-reranker-base`，
可用 RAG_RERANK_MODEL 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

from sentence_transformers import CrossEncoder

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RERANK = _ROOT / "server" / "knowledge" / "models" / "bge-reranker-base"

_cross_encoder: CrossEncoder | None = None


def _resolve_model_path() -> str:
    override = (os.environ.get("RAG_RERANK_MODEL") or "").strip()
    if override:
        return override
    if not _DEFAULT_RERANK.is_dir():
        raise FileNotFoundError(
            f"重排模型目录不存在: {_DEFAULT_RERANK}\n"
            "请把 bge-reranker-base 放到 server/knowledge/models/ 下，"
            "或设置 RAG_RERANK_MODEL。"
        )
    return str(_DEFAULT_RERANK)


def _get_cross_encoder() -> CrossEncoder:
    global _cross_encoder
    if _cross_encoder is None:
        path = _resolve_model_path()
        print(f"加载重排模型: {path}", flush=True)
        _cross_encoder = CrossEncoder(path)
    return _cross_encoder


def rerank(
    query: str,
    hits: list[dict],
    *,
    top_k: int = 5,
) -> list[dict]:
    """
    对召回 hits（需含 text）重排，写入 score，返回 top_k。
    hits 为空时原样返回。
    """
    if not hits:
        return []
    pairs = [(query, h.get("text") or "") for h in hits]
    scores = _get_cross_encoder().predict(pairs)
    ranked = sorted(
        zip(hits, scores),
        key=lambda item: float(item[1]),
        reverse=True,
    )
    out: list[dict] = []
    for hit, score in ranked[:top_k]:
        item = dict(hit)
        item["score"] = float(score)
        out.append(item)
    return out
