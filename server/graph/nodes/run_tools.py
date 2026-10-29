
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from server.evidence.pack import (
    absorb_tool_into_evidence,
    compact_tool_content_for_llm,
    focus_names_from_messages,
)
from server.infra import run_log_manager as tool_run_log


def _args_for(messages, tool_msg: ToolMessage):
    tid = getattr(tool_msg, "tool_call_id", None)
    for m in reversed(messages or []):
        if not isinstance(m, AIMessage):
            continue
        for tc in getattr(m, "tool_calls", None) or []:
            if tid and tc.get("id") == tid:
                return tc.get("args")
        break
    return None


async def run_tools(
    messages: list,
    tool_list: list,
    *,
    evidence_sink: dict | None = None,
) -> list:
    """执行 messages 里最后一条 AIMessage 的 tool_calls，返回新的 ToolMessage 列表。

    完整回包写入 evidence_sink；时间线工具摘要由 stream_bridge 落盘。
    返回给 ReAct 的 content 已压缩，避免下一轮 LLM 把巨型 JSON 再计一遍。
    """
    inner = ToolNode(tool_list)
    out = await inner.ainvoke({"messages": messages})
    out_list = list(out.get("messages") or [])
    # 同批 find 的原文先并入 focus，再压 list（顺序不定）
    batch_finds = [
        m
        for m in out_list
        if isinstance(m, ToolMessage)
        and (getattr(m, "name", None) or "") == "unity_find_game_object"
    ]
    focus = focus_names_from_messages(list(messages or []) + batch_finds)
    new_msgs = []
    for m in out_list:
        if not isinstance(m, ToolMessage):
            new_msgs.append(m)
            continue
        name = getattr(m, "name", None) or "tool"
        raw = m.content
        # dump_tool 现为 no-op，保留调用以免旧路径假定存在
        tool_run_log.dump_tool(name, raw, args=_args_for(messages, m))
        if evidence_sink is not None:
            absorb_tool_into_evidence(evidence_sink, name, raw)
        compact = compact_tool_content_for_llm(name, raw, focus_names=focus)
        new_msgs.append(
            ToolMessage(
                content=compact,
                name=name,
                tool_call_id=getattr(m, "tool_call_id", "") or "",
            )
        )
    return new_msgs
