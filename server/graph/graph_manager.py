from server import state
from langgraph.graph import StateGraph, START

from server.infra.checkpoint_manager import get_checkpointer, init_checkpoint
from server.graph.node_logging import with_node_log
from server.graph.nodes.greet_node import greet_node
from server.graph.nodes.hitl_node import hitl_node
from server.graph.nodes.intent_node import intent_node
from server.graph.nodes.analyze_node import analyze_node
from server.graph.nodes.act_node import act_node
from server.graph.flow import route_from_intent

# 完成扮演 register（report / stream hooks）
import server.roles  # noqa: F401

_graph = None


async def init_graph():
    """瘦图：greet → hitl → intent → analyze|act；act 前再确认。"""
    global _graph
    await init_checkpoint(use_sqlite=True)

    workflow = StateGraph(state.AgentState)

    workflow.add_node("greet", with_node_log("greet", greet_node))
    workflow.add_node("hitl", with_node_log("hitl", hitl_node))
    workflow.add_node("intent", with_node_log("intent", intent_node))
    workflow.add_node("analyze", with_node_log("analyze", analyze_node))
    workflow.add_node("act", with_node_log("act", act_node))

    workflow.add_edge(START, "greet")
    workflow.add_edge("greet", "hitl")
    workflow.add_edge("hitl", "intent")
    workflow.add_conditional_edges(
        "intent",
        route_from_intent,
        {
            "analyze": "analyze",
            "act": "act",
        },
    )
    workflow.add_edge("analyze", "hitl")
    workflow.add_edge("act", "hitl")

    _graph = workflow.compile(
        checkpointer=get_checkpointer(),
        # hitl：等人说话；act：改场景前确认
        interrupt_before=["hitl", "act"],
    )


def get_graph():
    if _graph is None:
        raise RuntimeError("graph 未初始化，请先调用 init_graph()")
    return _graph
