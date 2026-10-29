"""检索入口：可选英文改写 → 多集合向量粗召回 → CrossEncoder 重排。"""
from __future__ import annotations

import asyncio

from server.rag.rag_query import to_english_search_query
from server.rag.rag_rerank import rerank
from server.rag.rag_store import DEFAULT_COLLECTION, _embed, get_collection

# unity_docs 为主；renderdoc_docs 有则一并粗召回（安装目录 CHM 导入后建库）。
DEFAULT_COLLECTIONS: tuple[str, ...] = (DEFAULT_COLLECTION, "renderdoc_docs")


def _recall_one(
    collection_name: str,
    q_emb: list[list[float]],
    n: int,
) -> list[dict]:
    col = get_collection(collection_name, create=False)
    if col is None:
        return []
    # Chroma：n 不能超过集合条数
    try:
        total = col.count()
    except Exception:
        total = n
    if total <= 0:
        return []
    n_use = min(n, total)
    result = col.query(query_embeddings=q_emb, n_results=n_use)
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    hits: list[dict] = []
    for i in range(len(docs)):
        meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        hits.append(
            {
                "text": docs[i],
                "source": meta.get("source") or "",
                "distance": dists[i] if i < len(dists) else None,
                "collection": collection_name,
            }
        )
    return hits


def search(
    query: str,
    *,
    top_k: int = 5,
    recall_k: int = 20,
    collections: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """
    同步检索（调用方已准备好英文或原文 query）。
    对 collections 各自粗召回 recall_k，合并去重后 rerank 到 top_k。
    默认 collections=["unity_docs"]。
    每条含 text / source / distance / score / collection。
    """
    names = list(collections) if collections else list(DEFAULT_COLLECTIONS)
    names = [n.strip() for n in names if n and str(n).strip()]
    if not names:
        names = list(DEFAULT_COLLECTIONS)

    n = max(top_k, recall_k)
    q_emb = _embed([query])
    merged: list[dict] = []
    seen: set[str] = set()
    for name in names:
        for h in _recall_one(name, q_emb, n):
            key = f"{h.get('collection')}|{h.get('source')}|{(h.get('text') or '')[:120]}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)

    if not merged:
        return []

    # 多集合时先按向量距离粗排，再交给 rerank（避免候选过多）
    def _dist_key(h: dict):
        d = h.get("distance")
        return d if isinstance(d, (int, float)) else 1e9

    merged.sort(key=_dist_key)
    cand = merged[: max(n * len(names), n)]
    return rerank(query, cand, top_k=top_k)


async def asearch(
    query: str,
    *,
    top_k: int = 5,
    recall_k: int = 20,
    collections: list[str] | tuple[str, ...] | None = None,
    llm=None,
) -> tuple[list[dict], str]:
    """
    异步检索：若提供 llm，先把中文等问题改写成英文检索句再 search。
    collections 默认 ["unity_docs"]。
    返回 (hits, 实际用于检索的 query)。
    """
    q = (query or "").strip()
    if llm is not None and q:
        q = await to_english_search_query(llm, q)
    cols = collections
    hits = await asyncio.to_thread(
        lambda: search(
            q, top_k=top_k, recall_k=recall_k, collections=cols
        )
    )
    return hits, q
