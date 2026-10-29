from typing import Annotated, Any
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def _last_value(_old: Any, new: Any) -> Any:
    """后写覆盖：避免 Command/多节点同 step 双写非累加字段时报 INVALID_CONCURRENT。"""
    return new


class AgentState(TypedDict, total=False):
    """Unity 助手共用状态。"""

    messages: Annotated[list, add_messages]
    role: Annotated[str, _last_value]
    phase: Annotated[str, _last_value]
    # intent 分流：analyze | act（旧 checkpoint 里的 perceive 在路由时并进 analyze）
    route: Annotated[str, _last_value]
    act_request: Annotated[str, _last_value]
    approval: Annotated[dict[str, Any], _last_value]
    last_observation: Annotated[dict[str, Any], _last_value]
    # 本局证据袋：kind → {source, raw, summary}
    evidence: Annotated[dict[str, Any], _last_value]
    session_prefs: Annotated[dict[str, Any], _last_value]
    scene_id: Annotated[str, _last_value]
    # 分析站：summary / last_answer / mode
    analyze: Annotated[dict[str, Any], _last_value]
    # 兼容旧 checkpoint 字段（可读可忽略；新图不再写入）
    report: Annotated[dict[str, Any], _last_value]
    plan: Annotated[dict[str, Any], _last_value]
