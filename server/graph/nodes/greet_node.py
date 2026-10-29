"""开场站：自我介绍 + 引导；不调 Unity。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, SystemMessage

from server import state
from server.graph.llm import get_model
from server.prompts import build_system_for, greet_fallback, greet_hint
from server.roles.defaults import DEFAULT_ROLE
from server.roles.registry.context import build_session_context

phase_name = "greet"


async def greet_node(agent_state: state.AgentState):
    role = agent_state.get("role") or DEFAULT_ROLE
    model = get_model()
    sys = [
        SystemMessage(content=build_system_for(role)),
        SystemMessage(content=greet_hint(role)),
    ]
    session_ctx = await build_session_context(role)
    if session_ctx:
        sys.append(SystemMessage(content=session_ctx))
    full = await model.ainvoke(sys)
    text = (getattr(full, "content", None) or "").strip()
    if not text:
        text = greet_fallback(role)
    elif not text.startswith("【开场】"):
        text = "【开场】" + text
    return {
        "phase": phase_name,
        "messages": [AIMessage(content=text)],
    }
