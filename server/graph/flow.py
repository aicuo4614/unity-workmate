"""扮演流程能力：哪些站启用；intent 分流辅助。

瘦图：greet → hitl → intent → analyze|act →（回 hitl）。
只读（含整场分析）一律 analyze，由模型在对话里挑工具。
"""
from __future__ import annotations

from server.roles.defaults import DEFAULT_ROLE

# 各扮演启用的能力（未列出的站视为关闭）
_ROLE_CAPS: dict[str, dict[str, bool]] = {
    "scene_perf": {
        "greet": True,
        "analyze": True,
        "act": True,
    },
    # 真机：只对话。执行站关掉，intent 不会路由过去。
    "device_perf": {
        "greet": True,
        "analyze": True,
        "act": False,
    },
}


def role_caps(role: str | None) -> dict[str, bool]:
    return dict(_ROLE_CAPS.get(role or DEFAULT_ROLE) or {})


def can_act(role: str | None) -> bool:
    return bool(role_caps(role).get("act"))


def allowed_intent_routes(role: str | None) -> set[str]:
    """intent 可选下一站：按扮演 caps；至少保留 analyze。"""
    caps = role_caps(role)
    out: set[str] = set()
    for name in ("analyze", "act"):
        if caps.get(name):
            out.add(name)
    if not out:
        out.add("analyze")
    return out


def route_from_intent(state: dict) -> str:
    """intent 之后：读 state.route。"""
    r = str((state or {}).get("route") or "analyze")
    # 旧会话可能仍写出 perceive；只读已并进 analyze
    if r == "perceive":
        return "analyze"
    if r in ("analyze", "act"):
        return r
    return "analyze"
