"""执行站占位：批准之后停在这里。不绑工具、不改场景。

怎么执行还没设计。不要在本节点列 MCP 白名单或调用写入工具。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage

from server import state
from server.graph.flow import can_act
from server.roles.defaults import DEFAULT_ROLE

phase_name = "act"


async def act_node(agent_state: state.AgentState):
    role = agent_state.get("role") or DEFAULT_ROLE
    if not can_act(role):
        return {
            "phase": phase_name,
            "messages": [AIMessage(content="【执行】当前扮演不允许改场景。")],
            "approval": {"action": "denied", "source": "role"},
        }

    approval = agent_state.get("approval") or {}
    request = str(
        agent_state.get("act_request") or approval.get("request") or ""
    ).strip()

    if approval.get("action") != "approve":
        return {
            "phase": phase_name,
            "messages": [
                AIMessage(
                    content=(
                        f"【执行】未获批准（approval={approval.get('action') or '空'}），"
                        "未改场景。"
                    )
                )
            ],
        }

    if not request:
        spoken = "【执行】缺少要改的具体内容，未改场景。"
    else:
        spoken = (
            f"【执行】已记下「{request[:80]}」。"
            "执行还没做，场景未改。改法仍以分析站给出的方案为准。"
        )
    return {
        "phase": phase_name,
        "messages": [AIMessage(content=spoken)],
        "approval": {**approval, "action": "done"},
        "act_request": "",
    }
