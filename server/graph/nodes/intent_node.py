"""意图分流：LLM 读扮演 hint 选下一站；不调 Unity。

写出 state.route ∈ {analyze, act}；若 act 则附 act_request。
只读（含整场分析）一律 analyze。
场景语义 / 话术示例只进 prompts/{role}_intent.md，禁止焊进本文件。
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from server import state
from server.graph.flow import allowed_intent_routes, can_act
from server.graph.llm import get_model
from server.prompts import intent_hint
from server.roles.defaults import DEFAULT_ROLE

phase_name = "intent"

_FALLBACK_HINT = """
可选 route：analyze=提问、追问、怎么办、整场分析（只给方案不写入）；act=明确下令改场景。
「如何/怎么办/分析整个场景」→ analyze，不要 act。
只输出 JSON：{"route":"analyze|act","act_request":"若 act 则摘要改什么，否则空串"}。
不确定时用 analyze；禁止编造 act 对象。
"""


def _last_user_text(agent_state: state.AgentState) -> str:
    for m in reversed(agent_state.get("messages") or []):
        if isinstance(m, HumanMessage):
            return str(getattr(m, "content", "") or "").strip()
        if getattr(m, "type", None) == "human":
            return str(getattr(m, "content", "") or "").strip()
    return ""


def _parse_route(raw: str, *, allowed: set[str], user_text: str) -> tuple[str, str]:
    m = re.search(r"\{[\s\S]*\}", (raw or "").strip())
    if not m:
        return "analyze", ""
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return "analyze", ""
    route = str(data.get("route") or "analyze")
    if route not in allowed:
        route = "analyze" if "analyze" in allowed else next(iter(allowed), "analyze")
    req = str(data.get("act_request") or "").strip()
    if route == "act":
        if not req:
            req = user_text
        if len(req) < 4:
            return ("analyze" if "analyze" in allowed else route), ""
        return route, req
    return route, ""


async def _llm_route(text: str, role: str) -> tuple[str, str]:
    allowed = allowed_intent_routes(role)
    allow_act = can_act(role)
    role_hint = intent_hint(role) or _FALLBACK_HINT
    sys = SystemMessage(
        content=(
            "你是意图分流器。根据用户一句中文选择下一站；"
            "不要解释、不要 markdown，只输出一个 JSON 对象。\n"
            f"本扮演允许的 route：{sorted(allowed)}\n"
            f"本扮演是否允许 act：{allow_act}\n"
            f"—— 扮演专属提示 ——\n{role_hint}"
        )
    )
    full = await get_model().ainvoke([sys, HumanMessage(content=text or "（空）")])
    raw = (getattr(full, "content", None) or "").strip()
    return _parse_route(raw, allowed=allowed, user_text=text)


async def intent_node(agent_state: state.AgentState):
    role = agent_state.get("role") or DEFAULT_ROLE
    text = _last_user_text(agent_state)
    # 空句：不浪费调用，直接进分析等用户说清楚
    if not text.strip():
        route, act_request = "analyze", ""
        if "analyze" not in allowed_intent_routes(role):
            route = next(iter(allowed_intent_routes(role)), "analyze")
    else:
        route, act_request = await _llm_route(text, role)

    update: dict = {
        "phase": phase_name,
        "route": route,
        "act_request": act_request,
    }
    if route == "act":
        update["approval"] = {
            "action": "pending",
            "request": act_request,
            "source": "intent",
        }
    return update
