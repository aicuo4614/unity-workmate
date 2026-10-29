"""跑图入口：开局 greet；hitl 上说人话；act 前批准。"""
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from server.infra.model_manager import with_model_snapshot
from server.graph.graph_manager import get_graph
from server.graph.node_logging import reset_route
from server.graph.stream_bridge import iter_stream_events
from server.infra.app_logging import get_logger
from server.infra import run_log_manager as tool_run_log
from server.roles.defaults import DEFAULT_ROLE
from server.state import AgentState

_log = get_logger("ailearning.runner")


def _thread_config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 80,
    }


def _initial_state(
    role: str,
    session_prefs: dict | None = None,
    *,
    user_message: BaseMessage | None = None,
) -> AgentState:
    prefs = session_prefs or {}
    st: AgentState = {
        "messages": [user_message] if user_message is not None else [],
        "role": role or DEFAULT_ROLE,
        "phase": "greet",
        "route": "",
        "act_request": "",
        "approval": {},
        "last_observation": {},
        "evidence": {},
        "session_prefs": prefs,
        "scene_id": str(prefs.get("scene_id") or ""),
        "analyze": {},
    }
    return st


async def _resolve_role(
    g: CompiledStateGraph[AgentState, None, AgentState, AgentState],
    thread_id: str,
    fallback: str | None,
) -> str:
    snap = await g.aget_state(_thread_config(thread_id))
    values = snap.values or {}
    return str(values.get("role") or fallback or DEFAULT_ROLE)


@with_model_snapshot
async def req_stream(
    message: dict,
    thread_id: str = "default",
    agent_role: str | None = None,
    session_prefs: dict | None = None,
):
    """新会话：从 greet 开跑（可不带用户句）；已有 thread 不应再走本入口。"""
    g = get_graph()
    config = _thread_config(thread_id)
    snap = await g.aget_state(config)
    role = agent_role or DEFAULT_ROLE
    tool_run_log.begin_run(thread_id, str(message.get("content") or ""), kind="stream")
    reset_route(None)
    _log.info("stream start thread=%s role=%s", thread_id, role)

    if snap.values:
        # 已有会话：只追加用户句并进 intent（兼容误调）
        text = str(message.get("content") or "").strip()
        if not text:
            from common.stream_events import StreamEvent, StreamEventType

            yield StreamEvent(
                type=StreamEventType.ERROR,
                content="会话已存在，请用 resume 发送内容。",
            ).to_sse_data()
            yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
            return
        await g.aupdate_state(
            config,
            {"messages": [HumanMessage(content=text)]},
            as_node="hitl",
        )
        graph_input = Command(goto="intent")
        role = await _resolve_role(g, thread_id, role)
    else:
        # 新建：不塞假用户句，直接 greet
        graph_input = _initial_state(role, session_prefs)

    async for ev in iter_stream_events(
        g,
        graph_input,
        config,
        role=role,
        thread_id=thread_id,
    ):
        yield ev.to_sse_data()


@with_model_snapshot
async def req_resume_stream(
    *,
    thread_id: str,
    action: str,
    step_index: int = 0,
    content: str = "",
):
    from common.stream_events import StreamEvent, StreamEventType

    g = get_graph()
    config = _thread_config(thread_id)
    snap = await g.aget_state(config)
    next_nodes = list(snap.next or ())
    tool_run_log.begin_run(
        thread_id,
        content or action,
        kind=f"resume:{action}",
    )
    _log.info("resume thread=%s action=%s next=%s", thread_id, action, next_nodes)

    # —— 等人：interrupt_before hitl ——
    if "hitl" in next_nodes:
        if action not in ("say", "ask"):
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content=f"当前在等待输入，请用 say/ask，收到: {action}",
            ).to_sse_data()
            yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
            return
        text = (content or "").strip()
        if not text:
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content="内容为空。",
            ).to_sse_data()
            yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
            return
        reset_route("hitl")
        # 写入用户句后直跳 intent（不把 update 记在打断点上）
        await g.aupdate_state(
            config,
            {"messages": [HumanMessage(content=text)]},
            as_node="hitl",
        )
        graph_input = Command(goto="intent")

    # —— 改场景前：interrupt_before act ——
    elif "act" in next_nodes:
        if action == "approve":
            reset_route("intent")
            req = str((snap.values or {}).get("act_request") or "")
            await g.aupdate_state(
                config,
                {
                    "approval": {
                        "action": "approve",
                        "request": req,
                        "source": "hitl",
                        "step_index": step_index,
                    }
                },
            )
            graph_input = None
        elif action in ("skip", "reject"):
            reset_route("intent")
            await g.aupdate_state(
                config,
                {
                    "approval": {"action": "skip", "source": "hitl"},
                    "act_request": "",
                    "route": "analyze",
                },
            )
            graph_input = Command(goto="hitl")
        elif action in ("say", "ask"):
            # 改主意：当追问，回分析环
            text = (content or "").strip()
            if not text:
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    content="内容为空。",
                ).to_sse_data()
                yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
                return
            reset_route("hitl")
            await g.aupdate_state(
                config,
                {
                    "messages": [HumanMessage(content=text)],
                    "approval": {"action": "skip", "source": "redirect"},
                    "act_request": "",
                },
                as_node="hitl",
            )
            graph_input = Command(goto="intent")
        else:
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content=f"执行前仅支持 approve/skip/say，收到: {action}",
            ).to_sse_data()
            yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
            return
    else:
        yield StreamEvent(
            type=StreamEventType.ERROR,
            content=(
                "当前没有等待输入或批准的运行。"
                f"（next={next_nodes or '空'}；若刚改过图，请新开一局）"
            ),
        ).to_sse_data()
        yield StreamEvent(type=StreamEventType.RUN_END).to_sse_data()
        return

    role = await _resolve_role(g, thread_id, None)
    async for ev in iter_stream_events(
        g,
        graph_input,
        config,
        role=role,
        thread_id=thread_id,
    ):
        yield ev.to_sse_data()


async def req_thread_gate(thread_id: str) -> dict:
    g = get_graph()
    config = _thread_config(thread_id)
    snap = await g.aget_state(config)
    next_nodes = list(snap.next or ())
    values = snap.values or {}
    phase = values.get("phase")
    phase_s = str(phase) if phase else None
    act_req = str(values.get("act_request") or "")

    if "hitl" in next_nodes:
        return {
            "waiting": True,
            "gate": "hitl",
            "need_clarify": True,
            "need_approval": False,
            "plan_summary": "",
            "phase": phase_s or "hitl",
        }
    if "act" in next_nodes:
        return {
            "waiting": True,
            "gate": "act",
            "need_clarify": False,
            "need_approval": True,
            "plan_summary": act_req,
            "phase": phase_s or "act",
        }
    return {
        "waiting": False,
        "gate": None,
        "need_clarify": False,
        "need_approval": False,
        "plan_summary": "",
        "phase": phase_s,
    }


# 兼容旧 listener 非流式
@with_model_snapshot
async def req_nostream(
    message: dict,
    thread_id: str = "default",
    agent_role: str | None = None,
    session_prefs: dict | None = None,
):
    g = get_graph()
    tool_run_log.begin_run(thread_id, str(message.get("content") or ""), kind="chat")
    reset_route(None)
    role = agent_role or DEFAULT_ROLE
    text = str(message.get("content") or "").strip()
    config = _thread_config(thread_id)
    snap = await g.aget_state(config)
    if not snap.values:
        return await g.ainvoke(_initial_state(role, session_prefs), config=config)
    if text:
        await g.aupdate_state(
            config,
            {"messages": [HumanMessage(content=text)]},
            as_node="hitl",
        )
        return await g.ainvoke(Command(goto="intent"), config=config)
    return await g.ainvoke(None, config=config)
