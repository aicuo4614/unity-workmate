"""LangGraph astream_events → 我们的 StreamEvent。

职责：跑图时框架会吐出大量内部事件；这里只挑有用的，翻成
run_start / phase / tool_call / tool_result / token / observation / run_end，
给 SSE / client 用。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from common.stream_events import StreamEvent, StreamEventType

from server.roles.registry.stream import summarize_tool_result
from server.graph.spoken_text import has_tool_markup, strip_tool_markup
from server.infra import run_log_manager as session_log

# ---------------------------------------------------------------------------
# LangChain / LangGraph astream_events 约定的事件名（框架定的，不是大模型 API）
# 文档：Runnable.astream_events；禁止在下方逻辑里再写这些字符串字面量。
# ---------------------------------------------------------------------------
LC_ON_CHAIN_START = "on_chain_start"
LC_ON_CHAIN_END = "on_chain_end"
LC_ON_CHAT_MODEL_STREAM = "on_chat_model_stream"
LC_ON_CHAT_MODEL_END = "on_chat_model_end"
LC_ON_TOOL_START = "on_tool_start"
LC_ON_TOOL_END = "on_tool_end"

# 本图节点名（与 graph_manager 中 add_node 一致）
NODE_GREET = "greet"
NODE_HITL = "hitl"
NODE_INTENT = "intent"
NODE_ANALYZE = "analyze"
NODE_ACT = "act"

# 英文名 → 中文标题（给 PHASE 事件用；intent/hitl 不刷阶段条）
_PHASE_TITLES = {
    NODE_GREET: "开场",
    NODE_ANALYZE: "分析",
    NODE_ACT: "执行",
}


def _node_name(event: dict) -> str:
    """从 LangGraph 事件里解析当前节点名（优先 metadata.langgraph_node）。"""
    meta = event.get("metadata") or {}
    return str(meta.get("langgraph_node") or event.get("name") or "")


# 嵌套 LLM（如 RAG 英文化检索句）不得流到 client；见 rag_query.to_english_search_query
_UI_SILENT_TAGS = frozenset({"rag_rewrite", "nostream_ui"})


def _is_ui_silent(event: dict) -> bool:
    tags = event.get("tags") or []
    if isinstance(tags, (list, tuple, set)):
        return bool(_UI_SILENT_TAGS.intersection(tags))
    return False


def _tool_name(event: dict) -> str:
    """工具事件上的工具名。"""
    name = event.get("name")
    if name:
        return str(name)
    data = event.get("data") or {}
    for key in ("name", "tool"):
        if data.get(key):
            return str(data[key])
    return "tool"


def _tool_args(event: dict) -> dict[str, Any] | None:
    """on_tool_start 里的入参（有则带上，没有就 None）。"""
    data = event.get("data") or {}
    inp = data.get("input")
    if isinstance(inp, dict):
        return inp
    return None


def _tool_result_summary(role: str, name: str, output: Any) -> str:
    """工具回包缩成一行给 client（扮演可覆盖）。"""
    return summarize_tool_result(role, name, output)

def _spoken_from_node_output(output: Any) -> str | None:
    """节点 return 的 messages 里取最后一条有正文的 AI 口播（跳过纯 tool_calls）。"""
    if not isinstance(output, dict):
        return None
    msgs = output.get("messages") or []
    for last in reversed(msgs):
        if getattr(last, "tool_calls", None):
            continue
        text = getattr(last, "content", None)
        if isinstance(text, str) and text.strip():
            cleaned = strip_tool_markup(text)
            return cleaned or None
    return None


def _chunk_text(chunk: Any) -> str:
    """从 chat model stream chunk 取可见正文（兼容 str / content 块列表）。"""
    raw = getattr(chunk, "content", None) if chunk is not None else None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _chunk_has_tool_calls(chunk: Any) -> bool:
    """本 chunk 是否在拼 tool_calls（ReAct 中间轮勿当口播流出去）。"""
    if chunk is None:
        return False
    if getattr(chunk, "tool_call_chunks", None):
        return True
    if getattr(chunk, "tool_calls", None):
        return True
    add = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(add, dict) and (add.get("tool_calls") or add.get("function_call")):
        return True
    return False


def _message_has_tool_calls(msg: Any) -> bool:
    """完整 AIMessage 是否带真实 tool_calls。"""
    if msg is None:
        return False
    tcs = getattr(msg, "tool_calls", None) or []
    if tcs:
        return True
    add = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(add, dict) and (add.get("tool_calls") or add.get("function_call")):
        return True
    return False


def _message_text(msg: Any) -> str:
    """AIMessage / chunk 可见正文（与 _chunk_text 同形）。"""
    return _chunk_text(msg)


def _usage_from_chat_end(event: dict) -> tuple[int, int] | None:
    """从 on_chat_model_end 抠 input/output tokens；没有则 None。"""
    data = event.get("data") or {}
    output = data.get("output")
    prompt = 0
    completion = 0

    meta = getattr(output, "usage_metadata", None) if output is not None else None
    if isinstance(meta, dict):
        prompt = int(meta.get("input_tokens") or meta.get("prompt_tokens") or 0)
        completion = int(meta.get("output_tokens") or meta.get("completion_tokens") or 0)

    if prompt == 0 and completion == 0 and output is not None:
        resp = getattr(output, "response_metadata", None) or {}
        if isinstance(resp, dict):
            tu = resp.get("token_usage") or resp.get("usage") or {}
            if isinstance(tu, dict):
                prompt = int(tu.get("prompt_tokens") or tu.get("input_tokens") or 0)
                completion = int(
                    tu.get("completion_tokens") or tu.get("output_tokens") or 0
                )

    if prompt == 0 and completion == 0:
        return None
    return prompt, completion


async def iter_stream_events(
    g,
    graph_input: dict,
    config: dict,
    *,
    role: str,
    thread_id: str,
) -> AsyncIterator[StreamEvent]:
    """边跑图边产出 StreamEvent。

    参数:
        g: 已 compile 的图
        graph_input: 本轮喂给图的状态（首跑是整包 AgentState，续跑常只有 messages）
        config: 含 thread_id 等，给 checkpoint / get_state 用
        role: 扮演名，决定观测摘要用哪套钩子（如 scene_perf）
        thread_id: 会话 id，放进 RUN_START 给 client 展示

    产出顺序大致为:
        RUN_START → PHASE → (TOKEN|TOOL_CALL|TOOL_RESULT)… → OBSERVATION → …
        → RUN_END
    """
    # 1) 通知 client：本轮开始
    yield StreamEvent(
        type=StreamEventType.RUN_START,
        role=role,
        thread_id=thread_id,
    )
    # begin_run 由 runner 先调；此处只保证 thread 对齐（resume 同 thread 追加）

    # analyze 口播轮逐字流；工具轮靠 analyze_node 打 nostream_ui 静音。
    # 若偶发「先正文再 tool_calls」未打标，仍用 hold：已缓冲未推的丢弃，已推的无法收回。
    analyze_streamed_chars = 0
    analyze_buf = ""
    analyze_hold = False
    # greet 等非 analyze 节点：流过字则不再整包重推
    other_streamed_chars = 0

    # 2) 消费框架原始事件，翻译阶段 / 工具 / 吐字
    async for event in g.astream_events(
        graph_input,
        config=config,
        version="v2",
    ):
        kind = event.get("event")
        node = _node_name(event)

        if kind == LC_ON_CHAIN_START and node in _PHASE_TITLES:
            title = _PHASE_TITLES[node]
            session_log.log_phase(node, title)
            yield StreamEvent(
                type=StreamEventType.PHASE,
                name=node,
                title=title,
            )

        elif kind == LC_ON_TOOL_START:
            tname = _tool_name(event)
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL,
                name=tname,
                args=_tool_args(event),
            )

        elif kind == LC_ON_TOOL_END:
            tname = _tool_name(event)
            output = (event.get("data") or {}).get("output")
            summary = _tool_result_summary(role, tname, output)
            session_log.log_tool(tname, summary=summary)
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                name=tname,
                summary=summary,
            )

        # intent：结构分流不流；greet/act：结束再推（ainvoke 也会冒 stream，避免双开场）
        elif kind == LC_ON_CHAT_MODEL_STREAM:
            if _is_ui_silent(event):
                continue
            if node in (NODE_INTENT, NODE_ACT, NODE_HITL, NODE_GREET):
                continue
            chunk = (event.get("data") or {}).get("chunk")
            raw = _chunk_text(chunk)
            if node == NODE_ANALYZE:
                if _chunk_has_tool_calls(chunk):
                    analyze_hold = True
                    continue
                if not raw:
                    continue
                analyze_buf += raw
                if analyze_hold or has_tool_markup(raw) or has_tool_markup(analyze_buf):
                    analyze_hold = True
                    continue
                text = strip_tool_markup(raw)
                if text:
                    analyze_streamed_chars += len(text)
                    session_log.log_token(text)
                    yield StreamEvent(type=StreamEventType.TOKEN, content=text)
                continue
            if _chunk_has_tool_calls(chunk):
                continue
            if has_tool_markup(raw) and not strip_tool_markup(raw):
                continue
            text = strip_tool_markup(raw)
            if text:
                other_streamed_chars += len(text)
                session_log.log_token(text)
                yield StreamEvent(type=StreamEventType.TOKEN, content=text)

        elif kind == LC_ON_CHAT_MODEL_END:
            if _is_ui_silent(event):
                # 检索改写 / 工具轮不计入口播
                continue
            if node == NODE_ANALYZE:
                output = (event.get("data") or {}).get("output")
                # 本轮带工具：丢掉未推缓冲（已流出去的无法收回；工具轮应已 nostream）
                if (
                    analyze_hold
                    or _message_has_tool_calls(output)
                    or has_tool_markup(analyze_buf)
                ):
                    analyze_buf = ""
                    analyze_hold = False
                elif analyze_streamed_chars == 0:
                    # 未边流时（少见）补一整段
                    clean = strip_tool_markup(analyze_buf) or strip_tool_markup(
                        _message_text(output)
                    )
                    if clean:
                        analyze_streamed_chars += len(clean)
                        session_log.log_token(clean)
                        yield StreamEvent(type=StreamEventType.TOKEN, content=clean)
                    analyze_buf = ""
                    analyze_hold = False
                else:
                    analyze_buf = ""
                    analyze_hold = False
            usage = _usage_from_chat_end(event)
            if usage:
                prompt_n, completion_n = usage
                yield StreamEvent(
                    type=StreamEventType.USAGE,
                    prompt_tokens=prompt_n,
                    completion_tokens=completion_n,
                )

        elif kind == LC_ON_CHAIN_END and node in (
            NODE_GREET,
            NODE_ANALYZE,
            NODE_ACT,
        ):
            # 已流过字的站不要整包再推一遍（否则开场/分析会双份贴在一起）
            if node == NODE_ANALYZE and analyze_streamed_chars > 0:
                continue
            if node != NODE_ANALYZE and other_streamed_chars > 0:
                continue
            out = (event.get("data") or {}).get("output")
            spoken = _spoken_from_node_output(out)
            if spoken:
                session_log.log_token(spoken + "\n")
                yield StreamEvent(type=StreamEventType.TOKEN, content=spoken + "\n")

    snap = await g.aget_state(config)
    next_nodes = list(snap.next or ())
    values = snap.values or {}
    if "hitl" in next_nodes:
        session_log.log_gate("need_input", "gate=hitl")
        yield StreamEvent(
            type=StreamEventType.NEED_CLARIFY,
            gate="hitl",
            thread_id=thread_id,
        )
    elif "act" in next_nodes:
        req = str(values.get("act_request") or "")
        session_log.log_gate("need_approval", req[:80])
        yield StreamEvent(
            type=StreamEventType.NEED_APPROVAL,
            plan_summary=req,
            steps=[],
            thread_id=thread_id,
        )
    else:
        session_log.end_run()
        yield StreamEvent(type=StreamEventType.RUN_END)