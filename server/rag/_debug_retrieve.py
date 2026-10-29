"""临时：打印英文改写 + 粗召回 + 重排对照。"""
from __future__ import annotations

import asyncio

from server.rag.rag_query import to_english_search_query
from server.rag.rag_rerank import rerank
from server.rag.rag_store import _embed, _get_collection


def _short(src: str) -> str:
    s = (src or "").replace("\\", "/")
    for key in ("clean/", "extracted/"):
        if key in s:
            return s.split(key, 1)[-1]
    return s


def _preview(text: str, n: int = 240) -> str:
    t = (text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "…"


async def _maybe_rewrite(query: str) -> str:
    """有 ChatOpenAI 配置则改写；否则原样（便于离线看召回）。"""
    try:
        from common import config
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=config.settings.model,
            base_url=config.settings.base_url,
            api_key=config.settings.api_key,
        )
        return await to_english_search_query(llm, query)
    except Exception as e:
        print(f"(rewrite skipped: {e})")
        return query


async def _amain() -> None:
    raw = "URP 里透明物体怎么更早画？RenderQueue 该怎么改？"
    query = await _maybe_rewrite(raw)
    recall_k, top_k = 20, 5

    print("=" * 60)
    print("RAW:", raw)
    print("EN :", query)
    print("=" * 60)

    col = _get_collection()
    q_emb = _embed([query])
    result = col.query(query_embeddings=q_emb, n_results=recall_k)
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    hits: list[dict] = []
    print(f"\n--- ① 向量粗召回 top {len(docs)} ---\n")
    for i, doc in enumerate(docs):
        meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        src = meta.get("source") or ""
        dist = dists[i] if i < len(dists) else None
        dist_s = f"{dist:.4f}" if isinstance(dist, (int, float)) else str(dist)
        print(f"[{i + 1:02d}] dist={dist_s}")
        print(f"     src: {_short(src)}")
        print(f"     txt: {_preview(doc)}")
        print()
        hits.append({"text": doc, "source": src, "distance": dist})

    ranked = rerank(query, hits, top_k=top_k)
    print(f"\n--- ② 重排 top {len(ranked)} ---\n")
    for i, h in enumerate(ranked):
        score = h.get("score")
        dist = h.get("distance")
        score_s = f"{score:.4f}" if isinstance(score, (int, float)) else str(score)
        dist_s = f"{dist:.4f}" if isinstance(dist, (int, float)) else str(dist)
        print(f"[{i + 1}] score={score_s}  (原 dist={dist_s})")
        print(f"    src: {_short(h.get('source') or '')}")
        print(f"    txt: {_preview(h.get('text') or '', 300)}")
        print()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
