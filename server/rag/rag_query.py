"""检索查询改写：把用户问题转成适合英文 Unity 文档的检索句。

不做业务关键词特判；含中日韩等字符时才调 LLM，已是英文则原样。
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")

_REWRITE_SYS = """You rewrite the user's question into a short English search query for Unity engine documentation (Manual / Scripting API).
Rules:
- Output ONLY one English line; no quotes, no markdown, no explanation.
- Keep Unity technical terms as-is (URP, RenderQueue, ShaderLab, Batching, etc.).
- Prefer nouns and API/feature names over conversational filler.
- If the input is already good English for search, lightly tighten it; do not invent unrelated topics.
"""


def needs_english_rewrite(query: str) -> bool:
    return bool(_CJK_RE.search(query or ""))


async def to_english_search_query(llm, query: str) -> str:
    """
    异步：必要时用 LLM 译成英文检索句；失败则回退原文。
    llm 需支持 ainvoke([...messages])。
    """
    q = (query or "").strip()
    if not q:
        return q
    if not needs_english_rewrite(q):
        return q
    try:
        # tags：analyze 在 astream_events 里跑时，嵌套 ainvoke 仍会冒
        # on_chat_model_stream；stream_bridge 靠 rag_rewrite 把检索改写挡在 UI 外。
        full = await llm.ainvoke(
            [
                SystemMessage(content=_REWRITE_SYS),
                HumanMessage(content=q),
            ],
            config={
                "tags": ["rag_rewrite", "nostream_ui"],
                "run_name": "rag_rewrite",
            },
        )
        text = getattr(full, "content", None) or ""
        if isinstance(text, list):
            parts = []
            for block in text:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))
            text = "\n".join(parts)
        line = str(text).strip().splitlines()[0].strip().strip("\"'`")
        return line or q
    except Exception:
        return q
